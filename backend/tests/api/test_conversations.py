import asyncio
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
import uvicorn
from httpx import AsyncClient
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import Field

from app.agent.llm import OfflineChatModel
from app.api.dependencies import get_current_user
from app.config import Settings
from app.main import create_app
from app.models import User

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
SseEvent = tuple[str, dict[str, Any]]


# ---------- 测试用的替身模型 ----------


class RecordingChatModel(OfflineChatModel):
    """记录每次调用时收到的消息，用来检查交给模型的上下文。"""

    received: list[list[BaseMessage]] = Field(default_factory=list)

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.received.append(list(messages))
        async for chunk in super()._astream(messages, *args, **kwargs):
            yield chunk


class FailingChatModel(OfflineChatModel):
    """先输出一个字，然后模拟模型服务中途断开。"""

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        yield ChatGenerationChunk(message=AIMessageChunk(content="半"))
        raise RuntimeError("模型服务中途断开")


class GatedChatModel(OfflineChatModel):
    """输出第一个字后停下，等测试打开闸门才继续。"""

    started: asyncio.Event = Field(default_factory=asyncio.Event)
    gate: asyncio.Event = Field(default_factory=asyncio.Event)

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.started.set()
        first, *rest = "（离线模式）回复"
        yield ChatGenerationChunk(message=AIMessageChunk(content=first))
        await self.gate.wait()
        for character in rest:
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))


# ---------- 辅助函数 ----------


def parse_sse(body: str) -> list[SseEvent]:
    """把 SSE 响应体解析成 [(事件名, 数据), ...]。"""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def create_conversation(client: AsyncClient) -> int:
    response = await client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


async def send_message(client: AsyncClient, conversation_id: int, content: str) -> list[SseEvent]:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": content}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


async def get_history(client: AsyncClient, conversation_id: int) -> list[dict[str, Any]]:
    response = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    return response.json()


# ---------- 会话 ----------


async def test_new_conversation_is_listed_only_for_its_owner(open_client: OpenClient) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = await create_conversation(alice)

        alice_list = (await alice.get("/api/conversations")).json()
        bob_list = (await bob.get("/api/conversations")).json()

    assert [conversation["id"] for conversation in alice_list] == [conversation_id]
    assert alice_list[0]["title"] == "新会话"
    assert bob_list == []


async def test_history_of_other_users_conversation_is_not_found(open_client: OpenClient) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = await create_conversation(alice)

        response = await bob.get(f"/api/conversations/{conversation_id}/messages")

    assert response.status_code == 404


# ---------- 发送消息 ----------


async def test_reply_streams_as_accepted_then_deltas_then_done(client: AsyncClient) -> None:
    conversation_id = await create_conversation(client)

    events = await send_message(client, conversation_id, "你好")

    names = [name for name, _data in events]
    assert names[0] == "message.accepted"
    assert names[-1] == "message.done"
    deltas = [data["text"] for name, data in events if name == "message.delta"]
    assert len(deltas) > 1
    assert "".join(deltas) == "（离线模式）收到你的消息：你好"


async def test_history_after_done_has_user_message_then_reply(client: AsyncClient) -> None:
    conversation_id = await create_conversation(client)

    events = await send_message(client, conversation_id, "你好")

    accepted_id = events[0][1]["message_id"]
    done_id = events[-1][1]["message_id"]
    history = await get_history(client, conversation_id)
    assert [(m["id"], m["role"], m["content"]) for m in history] == [
        (accepted_id, "user", "你好"),
        (done_id, "assistant", "（离线模式）收到你的消息：你好"),
    ]


async def test_second_turn_gives_model_previous_turn_in_order(open_client: OpenClient) -> None:
    model = RecordingChatModel(chunk_delay_seconds=0)
    async with open_client(chat_model=model) as client:
        conversation_id = await create_conversation(client)
        await send_message(client, conversation_id, "第一句")
        await send_message(client, conversation_id, "第二句")

    second_call = model.received[1]
    assert [(message.type, message.text) for message in second_call[1:]] == [
        ("human", "第一句"),
        ("ai", "（离线模式）收到你的消息：第一句"),
        ("human", "第二句"),
    ]
    assert second_call[0].type == "system"


async def test_sending_to_other_users_conversation_is_not_found_and_writes_nothing(
    open_client: OpenClient,
) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = await create_conversation(alice)

        response = await bob.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": "你好"}
        )
        alice_history = await get_history(alice, conversation_id)

    assert response.status_code == 404
    assert alice_history == []


@pytest.mark.parametrize("content", ["", "   "], ids=["empty", "blank"])
async def test_blank_message_is_rejected_and_writes_nothing(client: AsyncClient, content: str) -> None:
    conversation_id = await create_conversation(client)

    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": content}
    )

    assert response.status_code == 422
    assert await get_history(client, conversation_id) == []


async def test_model_failure_sends_error_and_keeps_only_user_message(
    open_client: OpenClient,
) -> None:
    async with open_client(chat_model=FailingChatModel()) as client:
        conversation_id = await create_conversation(client)
        events = await send_message(client, conversation_id, "你好")
        history = await get_history(client, conversation_id)

    names = [name for name, _data in events]
    assert names[0] == "message.accepted"
    assert names[-1] == "error"
    assert "message.done" not in names
    assert [(m["role"], m["content"]) for m in history] == [("user", "你好")]


async def test_second_message_while_replying_is_rejected_and_writes_nothing(
    open_client: OpenClient,
) -> None:
    model = GatedChatModel(chunk_delay_seconds=0)
    async with open_client(chat_model=model) as client:
        conversation_id = await create_conversation(client)
        first = asyncio.create_task(send_message(client, conversation_id, "第一句"))
        try:
            async with asyncio.timeout(5):
                await model.started.wait()
                second = await client.post(
                    f"/api/conversations/{conversation_id}/messages", json={"content": "第二句"}
                )
        finally:
            model.gate.set()
            await first
        history = await get_history(client, conversation_id)

    assert second.status_code == 409
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "第一句"),
        ("assistant", "（离线模式）回复"),
    ]


# ---------- 真实 HTTP 流式 ----------


async def test_first_chunk_reaches_client_over_real_http_before_generation_finishes(
    test_settings: Settings, users: dict[str, User]
) -> None:
    model = GatedChatModel(chunk_delay_seconds=0)
    app = create_app(test_settings, model)
    app.dependency_overrides[get_current_user] = lambda: users["alice"]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    try:
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False) as client:
            conversation_id = await create_conversation(client)
            async with asyncio.timeout(5):
                async with client.stream(
                    "POST",
                    f"/api/conversations/{conversation_id}/messages",
                    json={"content": "你好"},
                ) as response:
                    lines = response.aiter_lines()
                    first_delta_received = False
                    async for line in lines:
                        if line == "event: message.delta":
                            first_delta_received = True
                            break
                    model.gate.set()
                    rest = [line async for line in lines]
    finally:
        model.gate.set()
        server.should_exit = True
        await server_task

    assert first_delta_received
    assert "event: message.done" in rest
