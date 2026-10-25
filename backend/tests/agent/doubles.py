"""Agent 测试用的替身：按剧本回复的模型、可控的工具箱。"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import Field

from app.agent.events import AgentEvent, DraftDiscarded, TextDelta

Respond = Callable[[list[BaseMessage]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """按测试写好的规则回复的模型。

    respond 收到这一轮的全部消息，返回模型这一轮要说的话（可以带工具调用）。
    每次收到的消息都记在 calls 里，方便检查交给模型的内容。
    流式输出时先逐字输出文字，再输出工具调用，和真实模型“先说一句再调用工具”的顺序一致。
    """

    respond: Respond
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    tool_names: tuple[str, ...] = ()

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any) -> "ScriptedChatModel":  # type: ignore[override]
        return self.model_copy(update={"tool_names": tuple(tool.name for tool in tools)})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply_to(messages))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        reply = self._reply_to(messages)
        for character in reply.text:
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))
        for index, call in enumerate(reply.tool_calls):
            chunk = {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": index}
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk]))

    def _reply_to(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        return self.respond(list(messages))


def calls_tools(*calls: tuple[str, dict[str, Any]], text: str = "") -> AIMessage:
    """模型这一轮要调用的工具。编号按顺序取 call-1、call-2……"""
    tool_calls = [{"name": name, "args": args, "id": f"call-{index}"} for index, (name, args) in enumerate(calls, 1)]
    return AIMessage(text, tool_calls=tool_calls)


def in_order(*replies: AIMessage) -> Respond:
    """模型按顺序说这几轮话，不管收到什么。"""
    remaining = iter(replies)
    return lambda _messages: next(remaining)


def always(reply: AIMessage) -> Respond:
    return lambda _messages: reply


class FakeToolbox:
    """不连 MCP 的工具箱：直接给出测试准备的工具，并记录打开和关闭的次数。"""

    def __init__(self, tools: Iterable[BaseTool]) -> None:
        self.tools = list(tools)
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, user_id: int) -> AsyncIterator[list[BaseTool]]:
        self.opened += 1
        try:
            yield self.tools
        finally:
            self.closed += 1


class UnreachableToolbox:
    """模拟 MCP Server 连不上。"""

    @asynccontextmanager
    async def open(self, user_id: int) -> AsyncIterator[list[BaseTool]]:
        raise ConnectionError("连不上 MCP Server")
        yield []  # 让它成为生成器函数，实际执行不到这里


def make_tool(name: str, run: Callable[..., Awaitable[str]]) -> BaseTool:
    """做一个工具。run 是工具真正执行的异步函数。"""
    return StructuredTool.from_function(coroutine=run, name=name, description=f"测试工具 {name}")


def counting_tool(name: str, result: str = '{"result": []}') -> tuple[BaseTool, list[int]]:
    """返回固定结果的工具，以及记录它被执行了几次的列表。"""
    runs: list[int] = []

    async def run() -> str:
        runs.append(1)
        return result

    return make_tool(name, run), runs


def blocking_tool(name: str, started: asyncio.Event) -> BaseTool:
    """开始执行后一直等下去的工具，用来测试超时和中途停止。"""

    async def run() -> str:
        started.set()
        await asyncio.Event().wait()
        return "不会走到这里"

    return make_tool(name, run)


def business_error_tool(name: str, message: str) -> BaseTool:
    """返回业务错误的工具（比如“订单不存在”），和 MCP 工具报业务错误时的表现一样。"""

    async def run() -> str:
        raise ToolException(message)

    return StructuredTool.from_function(
        coroutine=run, name=name, description=f"测试工具 {name}", handle_tool_error=True
    )

def preview_tool(preview: dict[str, Any]) -> tuple[BaseTool, list[dict[str, Any]]]:
    """模拟 MCP 的退货预览工具：正文给模型看，结构化结果放在 artifact 里。返回工具和每次收到的参数。"""
    received: list[dict[str, Any]] = []

    async def run(order_item_id: int, quantity: int, reason: str) -> tuple[str, dict[str, Any]]:
        received.append({"order_item_id": order_item_id, "quantity": quantity, "reason": reason})
        return json.dumps(preview, ensure_ascii=False), {"structured_content": preview}

    tool = StructuredTool.from_function(
        coroutine=run,
        name="preview_return_request",
        description="测试用的退货预览",
        response_format="content_and_artifact",
    )
    return tool, received
def final_text(events: Iterable[AgentEvent]) -> str:
    """按前端的规则拼出最终显示的回答：遇到 DraftDiscarded 就清空重来。"""
    text = ""
    for event in events:
        if isinstance(event, DraftDiscarded):
            text = ""
        elif isinstance(event, TextDelta):
            text += event.text
    return text
