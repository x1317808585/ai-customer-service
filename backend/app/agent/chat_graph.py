import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.events import TextDelta, ToolStatus

SYSTEM_PROMPT = (
    "你是电商平台的 AI 客服，用简洁、礼貌的中文回答顾客的问题。\n"
    "涉及商品、订单、退货、工单的信息，必须先用工具查询，不要猜测或编造。\n"
    "工具返回的金额单位是分，回答时换算成元。工具报错时，如实告诉顾客没有查到或查询失败。"
)
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

            write(ToolStatus(call_id, tool.name, "running"))
            try:
                async with asyncio.timeout(limits.tool_call_seconds):
                    message = await tool.ainvoke({**call, "id": call_id})
            except Exception as error:
                write(ToolStatus(call_id, tool.name, "failed"))
                raise ToolsUnavailableError(f"工具 {tool.name} 调用失败") from error
            failed = isinstance(message, ToolMessage) and message.status == "error"
            write(ToolStatus(call_id, tool.name, "failed" if failed else "succeeded"))
            results.append(message)
        return {"messages": results, "tool_rounds": state.get("tool_rounds", 0) + 1}

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


def _refused(call_id: str, reason: str) -> ToolMessage:
    """没有执行的工具调用也要回一条结果，模型接口要求每个调用都有对应的回复。"""
    return ToolMessage(reason, tool_call_id=call_id, status="error")
