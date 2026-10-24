import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.config import Settings

REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 2

# 离线模型的“规则”：消息里出现这些词，就调用对应的查询工具
OFFLINE_KEYWORD_TOOLS = {"订单": "list_my_orders", "退货": "list_my_returns", "工单": "list_my_tickets"}
OFFLINE_TOOL_LABELS = {"list_my_orders": "订单", "list_my_returns": "退货申请", "list_my_tickets": "工单"}


def create_chat_model(settings: Settings) -> BaseChatModel:
    """根据配置创建聊天模型。调用方只依赖 BaseChatModel，不关心具体是哪一家。"""
    if settings.llm_provider == "fake":
        return OfflineChatModel()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


class OfflineChatModel(BaseChatModel):
    """离线聊天模型：不联网、不需要 Key，按固定规则回复，用来在没有真实模型时走通整个流程。

    - 消息里有“订单”“退货”“工单”且绑定了对应工具时，调用那个查询工具；
    - 收到工具结果后，回复查到了几条记录；
    - 其他情况把用户的话按固定格式逐字返回。
    """

    chunk_delay_seconds: float = 0.03
    tool_names: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "offline"

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any) -> "OfflineChatModel":  # type: ignore[override]
        """记下可以用哪些工具，返回一个新的模型对象（不修改原来的）。"""
        return self.model_copy(update={"tool_names": tuple(tool.name for tool in tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._decide(messages))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        reply = self._decide(messages)
        if reply.tool_calls:
            call = reply.tool_calls[0]
            chunk = AIMessageChunk(
                content="",
                tool_call_chunks=[{"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": 0}],
            )
            yield ChatGenerationChunk(message=chunk)
            return
        for character in reply.text:
            await asyncio.sleep(self.chunk_delay_seconds)
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))

    def _decide(self, messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            return AIMessage(_summarize_tool_results(_trailing_tool_messages(messages)))

        question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
        for keyword, tool_name in OFFLINE_KEYWORD_TOOLS.items():
            if keyword in question and tool_name in self.tool_names:
                call_id = f"offline-{uuid4().hex[:8]}"
                return AIMessage("", tool_calls=[{"name": tool_name, "args": {}, "id": call_id}])
        return AIMessage(f"（离线模式）收到你的消息：{question}")


def _trailing_tool_messages(messages: list[BaseMessage]) -> list[ToolMessage]:
    trailing: list[ToolMessage] = []
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        trailing.insert(0, message)
    return trailing


def _summarize_tool_results(results: list[ToolMessage]) -> str:
    parts = []
    for result in results:
        label = OFFLINE_TOOL_LABELS.get(result.name or "", result.name or "数据")
        if result.status == "error":
            parts.append(f"查询{label}失败")
            continue
        records = _records_in(result)
        ids = "、".join(str(record["id"]) for record in records if "id" in record)
        parts.append(f"查到 {len(records)} 条{label}" + (f"（编号 {ids}）" if ids else ""))
    return "（离线模式）" + "；".join(parts) + "。"


def _records_in(result: ToolMessage) -> list[dict[str, Any]]:
    """从工具结果的附件里取出结构化数据：列表放在 result 字段里，单条记录就是一个对象。"""
    artifact = result.artifact if isinstance(result.artifact, dict) else {}
    data = artifact.get("structured_content")
    if isinstance(data, dict) and isinstance(data.get("result"), list):
        return [record for record in data["result"] if isinstance(record, dict)]
    return [data] if isinstance(data, dict) else []
