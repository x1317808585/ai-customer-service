import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, suppress
from typing import Any, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.tools import BaseTool

from app.agent.chat_graph import AgentLimits, ToolsUnavailableError, build_chat_graph
from app.agent.events import AgentEvent, DraftDiscarded, TextDelta

logger = logging.getLogger(__name__)

TOOLS_UNAVAILABLE_MESSAGE = "业务系统暂时无法访问，请稍后再试"
REPLY_TIMEOUT_MESSAGE = "回复超时，请稍后再试"


class Toolbox(Protocol):
    def open(self, user_id: int) -> AbstractAsyncContextManager[list[BaseTool]]: ...


class ReplyFailedError(Exception):
    """回复没能完成，消息可以直接给用户看（比如业务系统连不上、回复超时）。"""


class _Finished:
    """队列里的结束标记。"""


class ReplyAgent:
    """生成一条回复：打开当前用户的工具 → 运行对话图 → 把过程转换成事件。

    对外只有 stream_reply 一个方法。MCP 连接、令牌、工具循环、各种上限都藏在里面。
    """

    def __init__(self, model: BaseChatModel, toolbox: Toolbox, limits: AgentLimits | None = None) -> None:
        self._model = model
        self._toolbox = toolbox
        self._limits = limits or AgentLimits()

    async def stream_reply(self, user_id: int, history: list[BaseMessage]) -> AsyncIterator[AgentEvent]:
        """在单独的任务里生成回复，通过队列把事件交出来。

        MCP 连接的打开和关闭都在那个任务里完成；这里被取消（比如用户点了停止）时，
        会取消那个任务并等它把连接关好。
        """
        queue: asyncio.Queue[AgentEvent | _Finished] = asyncio.Queue()

        async def produce() -> None:
            try:
                await self._generate(user_id, history, queue.put_nowait)
            finally:
                queue.put_nowait(_Finished())

        worker = asyncio.create_task(produce())
        try:
            while not isinstance(event := await queue.get(), _Finished):
                yield event
            await worker  # 生成过程中的异常在这里重新抛出
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

    async def _generate(self, user_id: int, history: list[BaseMessage], emit: Callable[[AgentEvent], None]) -> None:
        try:
            async with asyncio.timeout(self._limits.reply_seconds):
                failure = await self._run_with_tools(user_id, history, emit)
        except TimeoutError:
            raise ReplyFailedError(REPLY_TIMEOUT_MESSAGE) from None
        if isinstance(failure, ToolsUnavailableError):
            raise ReplyFailedError(TOOLS_UNAVAILABLE_MESSAGE) from failure
        if failure is not None:
            raise failure

    async def _run_with_tools(
        self, user_id: int, history: list[BaseMessage], emit: Callable[[AgentEvent], None]
    ) -> Exception | None:
        """返回对话图里发生的异常（没有就返回 None）；MCP 连接本身出问题时抛出 ReplyFailedError。

        对话图里的异常要在离开 MCP 连接之前接住：异常一旦穿过 MCP 客户端，
        会被包装成 ExceptionGroup，就分不清是模型出错还是 MCP 出错了。
        """
        failure: Exception | None = None
        try:
            async with self._toolbox.open(user_id) as tools:
                try:
                    await self._run_graph(tools, history, emit)
                except Exception as error:
                    failure = error
        except Exception as error:
            logger.warning("用户 %s 的业务工具不可用：%r", user_id, error)
            raise ReplyFailedError(TOOLS_UNAVAILABLE_MESSAGE) from error
        return failure

    async def _run_graph(
        self, tools: list[BaseTool], history: list[BaseMessage], emit: Callable[[AgentEvent], None]
    ) -> None:
        graph = build_chat_graph(self._model, tools, self._limits)
        config: dict[str, Any] = {"recursion_limit": 2 * self._limits.max_tool_rounds + 4}
        draft = _Draft(emit)
        async for mode, payload in graph.astream(
            {"messages": history, "tool_rounds": 0}, config, stream_mode=["messages", "custom"]
        ):
            if mode == "custom":
                emit(payload)
                continue
            chunk, metadata = payload
            if isinstance(chunk, AIMessageChunk) and metadata.get("langgraph_node") == "model":
                draft.add(chunk, metadata["langgraph_step"])


class _Draft:
    """把模型的流式输出整理成事件。

    同一次模型调用（同一个 step）里一旦出现工具调用，这次调用说的话就不是最终回答：
    已经发出去的部分用 DraftDiscarded 撤回，后面的部分不再发出。
    """

    def __init__(self, emit: Callable[[AgentEvent], None]) -> None:
        self._emit = emit
        self._step: int | None = None
        self._has_text = False
        self._calls_tools = False

    def add(self, chunk: AIMessageChunk, step: int) -> None:
        if step != self._step:
            self._step, self._has_text, self._calls_tools = step, False, False
        if chunk.tool_call_chunks and not self._calls_tools:
            self._calls_tools = True
            if self._has_text:
                self._emit(DraftDiscarded())
        if chunk.text and not self._calls_tools:
            self._has_text = True
            self._emit(TextDelta(chunk.text))
