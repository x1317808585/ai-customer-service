"""SSE 心跳：长时间没有事件时补发心跳，不打断生成；顾客断开时照常停止，不保存未完成的回复。"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import Field

from app.agent.llm import OfflineChatModel
from app.api import conversations as conversations_api
from tests.api.chat_api import create_conversation, displayed_text, get_history, send_message

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
OpenServer = Callable[..., AbstractAsyncContextManager[AsyncClient]]

HEARTBEAT_SECONDS = 0.05


class SlowChatModel(OfflineChatModel):
    """先说一半，停顿一会儿（足够发出好几次心跳），再说完。"""

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        yield ChatGenerationChunk(message=AIMessageChunk(content="（离线模式）"))
        await asyncio.sleep(HEARTBEAT_SECONDS * 6)
        yield ChatGenerationChunk(message=AIMessageChunk(content="回复"))


class GatedChatModel(OfflineChatModel):
    """输出第一个字后停下，等测试打开闸门才继续。"""

    gate: asyncio.Event = Field(default_factory=asyncio.Event)

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        first, *rest = "（离线模式）回复"
        yield ChatGenerationChunk(message=AIMessageChunk(content=first))
        await self.gate.wait()
        for character in rest:
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))


@pytest.fixture(autouse=True)
def short_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conversations_api, "HEARTBEAT_SECONDS", HEARTBEAT_SECONDS)


async def test_slow_reply_gets_heartbeats_and_is_still_saved(open_client: OpenClient) -> None:
    async with open_client(chat_model=SlowChatModel(chunk_delay_seconds=0)) as client:
        conversation_id = await create_conversation(client)
        events = await send_message(client, conversation_id, "你好")
        history = await get_history(client, conversation_id)

    names = [name for name, _ in events]
    assert (names[0], names[-1]) == ("message.accepted", "message.done")
    assert ("heartbeat", {}) in events
    assert displayed_text(events) == "（离线模式）回复"
    assert [(m["role"], m["content"]) for m in history] == [("user", "你好"), ("assistant", "（离线模式）回复")]


async def test_disconnecting_while_heartbeats_flow_saves_no_partial_reply(open_server: OpenServer) -> None:
    model = GatedChatModel(chunk_delay_seconds=0)
    async with open_server(model) as client:
        conversation_id = await create_conversation(client)
        async with asyncio.timeout(5):
            async with client.stream(
                "POST", f"/api/conversations/{conversation_id}/messages", json={"content": "第一句"}
            ) as response:
                async for line in response.aiter_lines():
                    if line == "event: heartbeat":
                        break  # 已经在发心跳，说明回复卡在半路；这时断开
            model.gate.set()

            while True:
                retry = await client.post(
                    f"/api/conversations/{conversation_id}/messages", json={"content": "第二句"}
                )
                if retry.status_code != 409:
                    break
                await asyncio.sleep(0.05)
        history = await get_history(client, conversation_id)

    assert retry.status_code == 200
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "第一句"),
        ("user", "第二句"),
        ("assistant", "（离线模式）回复"),
    ]
