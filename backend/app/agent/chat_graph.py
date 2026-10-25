import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.events import ActionProposed, TextDelta, ToolStatus

SYSTEM_PROMPT = (
    "你是电商平台的 AI 客服，用简洁、礼貌的中文回答顾客的问题。\n"
    "涉及商品、订单、退货、工单的信息，必须先用工具查询，不要猜测或编造。\n"
    "工具返回的金额单位是分，回答时换算成元。工具报错时，如实告诉顾客没有查到或查询失败。\n"
    "以下操作都只能生成待确认的申请：页面上会出现确认卡片，顾客点击确认后才会执行，所以不要说已经完成。"
    "一次回复只能发起一项申请。\n"
    "- 申请退货：先查订单拿到要退商品的订单项编号（order_item_id，不是订单号），再用 preview_return_request。\n"
    "- 撤回退货申请：先用 list_my_returns 找到退货申请编号，再用 preview_return_cancellation。只有处理中的申请能撤回。\n"
    "- 转人工：顾客明确要求人工，或者遇到换货、质量争议、物流异常等工具处理不了的问题时，"
    "用 preview_ticket 整理问题摘要（第三人称，写清问题和诉求，有订单就带上订单号）。"
    "创建工单只是交给人工客服处理，不要说人工客服已经接入。"
)

# 预览工具 → 它发起的待确认操作类型。预览成功后，页面上会出现确认卡片
PROPOSAL_TOOLS = {
    "preview_return_request": "return_request",
    "preview_return_cancellation": "return_cancellation",
    "preview_ticket": "ticket",
}
PROPOSAL_NOTE = "已生成待确认的申请，页面上会显示确认卡片。顾客点击确认后才会执行，现在还没有执行。"
TOOL_LIMIT_REPLY = "抱歉，这个问题需要查询的内容太多，我暂时处理不了。可以换个问法，或者告诉我需要转人工。"


@dataclass(frozen=True)
class AgentLimits:
    """一次回复的上限。达到工具轮数上限时明确结束，不会无限循环。"""

    max_tool_rounds: int = 3  # 最多执行几轮工具调用（模型提一轮要求、我们执行一轮，算一轮）
    max_tool_calls_per_round: int = 3  # 每轮最多执行几个工具调用，多出来的不执行
    tool_call_seconds: float = 15  # 单次工具调用的超时
    reply_seconds: float = 120  # 整次回复的总时限，要小于 MCP 令牌的有效期（5 分钟）


class ToolsUnavailableError(Exception):
    """工具调用超时，或者 MCP 通信失败。这时不能让模型继续猜，整次回复按失败处理。"""


class AgentState(MessagesState):
    tool_rounds: int
    proposal_made: bool  # 这次回复是否已经发起过待确认操作


def build_chat_graph(
    model: BaseChatModel, tools: Sequence[BaseTool], limits: AgentLimits
) -> CompiledStateGraph:
    """搭建对话流程：模型 →（需要时）执行工具 → 模型 → …… → 最终回答。

    工具由调用方传入（每次回复都是当前用户专属的一组），所以每次回复都搭一张新图。
    """
    tools_by_name = {tool.name: tool for tool in tools}
    bound_model = model.bind_tools(list(tools)) if tools else model

    async def call_model(state: AgentState) -> dict[str, list[BaseMessage]]:
        reply = await bound_model.ainvoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    def after_model(state: AgentState) -> Literal["call_tools", "give_up", "__end__"]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return END
        if state.get("tool_rounds", 0) >= limits.max_tool_rounds:
            return "give_up"
        return "call_tools"

    async def call_tools(state: AgentState) -> dict[str, object]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        write = get_stream_writer()
        proposal_made = state.get("proposal_made", False)
        results: list[BaseMessage] = []
        for index, call in enumerate(last.tool_calls):
            call_id = call["id"] or f"call-{index}"
            tool = tools_by_name.get(call["name"])
            if index >= limits.max_tool_calls_per_round:
                results.append(_refused(call_id, f"一次最多查询 {limits.max_tool_calls_per_round} 项，这一项没有执行。"))
                continue
            if tool is None:
                results.append(_refused(call_id, f"没有名为 {call['name']} 的工具。"))
                continue
            if call["name"] in PROPOSAL_TOOLS and proposal_made:
                results.append(_refused(call_id, "这次回复已经发起过一项待确认的申请，请先让顾客确认或取消。"))
                continue

            write(ToolStatus(call_id, tool.name, "running"))
            try:
                async with asyncio.timeout(limits.tool_call_seconds):
                    message = await tool.ainvoke({**call, "id": call_id})
            except Exception as error:
                write(ToolStatus(call_id, tool.name, "failed"))
                raise ToolsUnavailableError(f"工具 {tool.name} 调用失败") from error
            failed = isinstance(message, ToolMessage) and message.status == "error"
            write(ToolStatus(call_id, tool.name, "failed" if failed else "succeeded"))
            if tool.name in PROPOSAL_TOOLS and not failed:
                write(ActionProposed(PROPOSAL_TOOLS[tool.name], dict(call["args"]), _structured(message)))
                message = _with_note(message, PROPOSAL_NOTE)
                proposal_made = True
            results.append(message)
        return {
            "messages": results,
            "tool_rounds": state.get("tool_rounds", 0) + 1,
            "proposal_made": proposal_made,
        }

    async def give_up(_state: AgentState) -> dict[str, list[BaseMessage]]:
        get_stream_writer()(TextDelta(TOOL_LIMIT_REPLY))
        return {"messages": [AIMessage(TOOL_LIMIT_REPLY)]}

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("call_tools", call_tools)
    graph.add_node("give_up", give_up)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model)
    graph.add_edge("call_tools", "model")
    graph.add_edge("give_up", END)
    return graph.compile()


def _structured(message: object) -> dict[str, Any]:
    """MCP 工具的结构化结果放在 ToolMessage 的 artifact 里。"""
    artifact = getattr(message, "artifact", None)
    data = artifact.get("structured_content") if isinstance(artifact, dict) else None
    if not isinstance(data, dict):
        raise ToolsUnavailableError("预览工具没有返回结构化结果")
    return data


def _with_note(message: ToolMessage, note: str) -> ToolMessage:
    """在工具结果后面补一句说明，交给模型看。"""
    content = message.content
    if isinstance(content, str):
        return message.model_copy(update={"content": f"{content}\n{note}"})
    return message.model_copy(update={"content": [*content, {"type": "text", "text": note}]})


def _refused(call_id: str, reason: str) -> ToolMessage:
    """没有执行的工具调用也要回一条结果，模型接口要求每个调用都有对应的回复。"""
    return ToolMessage(reason, tool_call_id=call_id, status="error")
