"""通过聊天接口验收知识库问答：分流正确，引用随回复保存，知识库重建后老回复的引用不变。"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import KnowledgeBase, KnowledgeDocument, read_documents
from app.models import Order, User
from tests.api.chat_api import SseEvent, create_conversation, displayed_text, get_history, send_message

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]


@pytest.fixture
def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    return KnowledgeBase(session_factory)


def done_citations(events: list[SseEvent]) -> list[dict[str, Any]]:
    name, data = events[-1]
    assert name == "message.done", events
    return data["citations"]


def policy(content: str) -> KnowledgeDocument:
    return KnowledgeDocument("return-policy", f"# 退货政策\n\n## 退货期限\n\n{content}\n")


async def test_policy_question_records_question_and_return_request_each_go_to_the_right_place(
    client: AsyncClient,
    knowledge: KnowledgeBase,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
) -> None:
    await knowledge.rebuild(read_documents())
    product = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, product, delivered_at=datetime.now(UTC) - timedelta(days=1))
    conversation_id = await create_conversation(client)

    policy_reply = await send_message(client, conversation_id, "退货政策是什么？")
    records_reply = await send_message(client, conversation_id, "我的退货申请处理到哪了？")
    return_reply = await send_message(client, conversation_id, f"帮我退货，订单项 {order.items[0].id}")

    assert displayed_text(policy_reply).startswith("（离线模式）根据《退货政策》“退货期限”：「签收后 7 天内可以申请退货")
    assert [citation["source_id"] for citation in done_citations(policy_reply)] == ["return-policy#退货期限"]
    assert displayed_text(records_reply) == "（离线模式）查到 0 条退货申请。"
    assert done_citations(records_reply) == []
    assert [name for name, _ in return_reply].count("action.proposed") == 1
    assert done_citations(return_reply) == []


async def test_reply_without_evidence_saves_no_citations(client: AsyncClient, knowledge: KnowledgeBase) -> None:
    await knowledge.rebuild(read_documents())
    conversation_id = await create_conversation(client)

    events = await send_message(client, conversation_id, "能开发票吗？")

    assert "没有找到" in displayed_text(events)
    assert done_citations(events) == []
    assert [message["citations"] for message in await get_history(client, conversation_id)] == [[], []]


async def test_old_replies_keep_their_citations_after_the_knowledge_base_is_rebuilt(
    client: AsyncClient, knowledge: KnowledgeBase
) -> None:
    old = {
        "source_id": "return-policy#退货期限",
        "document_title": "退货政策",
        "section": "退货期限",
        "content": "签收后 7 天内可以申请退货。",
    }
    new = {**old, "content": "签收后 15 天内可以申请退货。"}
    conversation_id = await create_conversation(client)

    await knowledge.rebuild([policy(old["content"])])
    first = await send_message(client, conversation_id, "退货政策是什么？")
    await knowledge.rebuild([policy(new["content"])])
    second = await send_message(client, conversation_id, "退货政策是什么？")

    assert (done_citations(first), done_citations(second)) == ([old], [new])
    history = await get_history(client, conversation_id)
    assert [message["citations"] for message in history if message["role"] == "assistant"] == [[old], [new]]
