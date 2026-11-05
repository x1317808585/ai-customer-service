"""长期偏好：从顾客的话里记下，新会话也能用上；只属于本人；删除后长期偏好块里不再有它。"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from httpx import AsyncClient
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import Field

from app.agent.llm import OfflineChatModel
from tests.api.chat_api import create_conversation, send_message

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]


class RecordingChatModel(OfflineChatModel):
    """记录每次调用时收到的消息，用来检查交给模型的上下文。"""

    received: list[list[BaseMessage]] = Field(default_factory=list)

    async def _astream(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self.received.append(list(messages))
        async for chunk in super()._astream(messages, *args, **kwargs):
            yield chunk


def context_for(model: RecordingChatModel, question: str) -> list[BaseMessage]:
    """模型回答这个问题时收到的上下文（这一轮第一次调用模型时的消息）。"""
    return next(messages for messages in model.received if messages[-1].text == question)


def long_term_block(context: list[BaseMessage]) -> str | None:
    return next(
        (m.text for m in context if isinstance(m, SystemMessage) and m.text.startswith("顾客的长期偏好")), None
    )


async def memories_of(client: AsyncClient) -> list[tuple[str, str]]:
    response = await client.get("/api/me/memories")
    assert response.status_code == 200
    return [(memory["label"], memory["value"]) for memory in response.json()]


async def test_preference_from_one_conversation_is_used_in_a_new_one_only_for_that_user(
    open_client: OpenClient,
) -> None:
    model = RecordingChatModel(chunk_delay_seconds=0)
    async with open_client(as_user="alice", chat_model=model) as alice, open_client(
        as_user="bob", chat_model=model
    ) as bob:
        await send_message(alice, await create_conversation(alice), "我更喜欢入耳式耳机")
        await send_message(alice, await create_conversation(alice), "alice 想买耳机")
        await send_message(bob, await create_conversation(bob), "bob 想买耳机")

        assert await memories_of(alice) == [("耳机佩戴方式", "入耳式")]
        assert await memories_of(bob) == []

    assert "耳机佩戴方式=入耳式" in (long_term_block(context_for(model, "alice 想买耳机")) or "")
    assert long_term_block(context_for(model, "bob 想买耳机")) is None


async def test_changing_ones_mind_keeps_only_the_latest_value(client: AsyncClient) -> None:
    conversation_id = await create_conversation(client)

    await send_message(client, conversation_id, "我更喜欢入耳式耳机")
    await send_message(client, conversation_id, "算了，我还是喜欢头戴式")

    assert await memories_of(client) == [("耳机佩戴方式", "头戴式")]


async def test_deleted_preference_leaves_the_long_term_block_but_history_stays(open_client: OpenClient) -> None:
    model = RecordingChatModel(chunk_delay_seconds=0)
    async with open_client(chat_model=model) as client:
        conversation_id = await create_conversation(client)
        await send_message(client, conversation_id, "我更喜欢入耳式耳机")
        [memory] = (await client.get("/api/me/memories")).json()

        deleted = await client.delete(f"/api/me/memories/{memory['id']}")
        await send_message(client, conversation_id, "同一个会话里再问")
        await send_message(client, await create_conversation(client), "新会话里再问")

    assert deleted.status_code == 204
    same = context_for(model, "同一个会话里再问")
    assert long_term_block(same) is None
    assert "我更喜欢入耳式耳机" in [m.text for m in same if isinstance(m, HumanMessage)]  # 原话仍是正常的聊天记录
    assert long_term_block(context_for(model, "新会话里再问")) is None


async def test_cannot_delete_someone_elses_preference(open_client: OpenClient) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        await send_message(alice, await create_conversation(alice), "我平时用茶轴")
        [memory] = (await alice.get("/api/me/memories")).json()

        response = await bob.delete(f"/api/me/memories/{memory['id']}")

        assert response.status_code == 404
        assert await memories_of(alice) == [("键盘轴体", "茶轴")]
