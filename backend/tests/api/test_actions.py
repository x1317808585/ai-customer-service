from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Order, Product, ReturnRequest, User
from tests.agent.doubles import ScriptedChatModel, calls_tools
from tests.api.chat_api import SseEvent, create_conversation, get_history, send_message

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]


def proposing_model(order_item_id: int, fail_after_proposal: bool = False) -> ScriptedChatModel:
    """第一轮发起退货预览；拿到预览结果后回复“请确认”（或者模拟模型报错）。其他时候正常回答。"""

    def respond(messages: list[BaseMessage]) -> AIMessage:
        last = messages[-1]
        if isinstance(last, ToolMessage):
            if fail_after_proposal:
                raise RuntimeError("模型服务中途断开")
            return AIMessage("已为你准备好退货申请，请在上方确认。")
        if last.text == "我要退货":
            return calls_tools(("preview_return_request", {"order_item_id": order_item_id, "quantity": 1, "reason": "坏了"}))
        return AIMessage("好的")

    return ScriptedChatModel(respond=respond)


@pytest.fixture
async def earbuds_item(users: dict[str, User], add_product: AddProduct, add_order: AddOrder) -> int:
    product = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, product, delivered_at=datetime.now(UTC) - timedelta(days=1))
    return order.items[0].id


async def ask_for_return(client: AsyncClient) -> tuple[int, list[SseEvent]]:
    """在新会话里说“我要退货”，返回会话 id 和收到的事件。"""
    conversation_id = await create_conversation(client)
    return conversation_id, await send_message(client, conversation_id, "我要退货")


def proposed_action(events: list[SseEvent]) -> dict[str, Any]:
    [data] = [data for name, data in events if name == "action.proposed"]
    return data["action"]


async def return_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(ReturnRequest))


# ---------- 提议 ----------


async def test_card_is_saved_as_its_own_message_and_shows_in_history(
    open_client: OpenClient, earbuds_item: int
) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item)) as client:
        conversation_id, events = await ask_for_return(client)
        history = await get_history(client, conversation_id)

    names = [name for name, _data in events]
    assert names.index("action.proposed") < names.index("message.done")
    action = proposed_action(events)
    assert action["status"] == "pending"
    assert action["preview"]["product_name"] == "SoundPods 降噪耳机"
    assert action["preview"]["refund_cents"] == 59900

    assert [(m["role"], m["action"] is not None) for m in history] == [
        ("user", False),
        ("assistant", True),  # 确认卡片
        ("assistant", False),  # 回复正文
    ]
    assert history[1]["action"] == action
    assert history[2]["content"] == "已为你准备好退货申请，请在上方确认。"


async def test_card_survives_when_the_reply_fails_after_the_proposal(
    open_client: OpenClient, earbuds_item: int
) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item, fail_after_proposal=True)) as client:
        conversation_id, events = await ask_for_return(client)
        history = await get_history(client, conversation_id)

    assert [name for name, _data in events][-1] == "error"
    assert [(m["role"], m["action"]["status"] if m["action"] else None) for m in history] == [
        ("user", None),
        ("assistant", "pending"),
    ]


# ---------- 确认和取消 ----------


async def test_confirm_submits_once_and_history_and_next_turn_see_the_result(
    open_client: OpenClient, earbuds_item: int, session_factory: SessionFactory
) -> None:
    model = proposing_model(earbuds_item)
    async with open_client(chat_model=model) as client:
        conversation_id, events = await ask_for_return(client)
        action_id = proposed_action(events)["id"]

        first = await client.post(f"/api/actions/{action_id}/confirm")
        again = await client.post(f"/api/actions/{action_id}/confirm")
        history = await get_history(client, conversation_id)
        await send_message(client, conversation_id, "提交成功了吗")

    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert first.json()["status"] == "executed"
    assert await return_count(session_factory) == 1
    assert history[1]["action"]["status"] == "executed"
    return_id = first.json()["result"]["return_id"]
    card_for_model = model.calls[-1][-3]  # 下一轮交给模型的历史里，卡片按当前状态描述
    assert f"已提交，退货单号 {return_id}" in card_for_model.text


async def test_business_rejection_returns_200_with_failed_status(
    open_client: OpenClient, earbuds_item: int, session_factory: SessionFactory
) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item)) as client:
        _conversation_id, events = await ask_for_return(client)
        action_id = proposed_action(events)["id"]
        async with session_factory() as session:  # 提议之后，商品被改成不支持退货
            await session.execute(update(Product).values(returnable=False))
            await session.commit()

        response = await client.post(f"/api/actions/{action_id}/confirm")

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "不支持退货" in response.json()["error"]
    assert await return_count(session_factory) == 0


async def test_cancelled_action_cannot_be_confirmed_and_409_carries_current_state(
    open_client: OpenClient, earbuds_item: int, session_factory: SessionFactory
) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item)) as client:
        _conversation_id, events = await ask_for_return(client)
        action_id = proposed_action(events)["id"]

        cancelled = await client.post(f"/api/actions/{action_id}/cancel")
        confirm = await client.post(f"/api/actions/{action_id}/confirm")

    assert cancelled.json()["status"] == "cancelled"
    assert confirm.status_code == 409
    assert confirm.json()["action"]["status"] == "cancelled"
    assert await return_count(session_factory) == 0


async def test_confirm_refuses_extra_fields_and_writes_nothing(
    open_client: OpenClient, earbuds_item: int, session_factory: SessionFactory
) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item)) as client:
        _conversation_id, events = await ask_for_return(client)
        action_id = proposed_action(events)["id"]

        response = await client.post(f"/api/actions/{action_id}/confirm", json={"quantity": 9})
        state = await client.get(f"/api/actions/{action_id}")

    assert response.status_code == 422
    assert state.json()["status"] == "pending"
    assert await return_count(session_factory) == 0


async def test_someone_elses_action_is_not_found(open_client: OpenClient, earbuds_item: int) -> None:
    async with (
        open_client(as_user="alice", chat_model=proposing_model(earbuds_item)) as alice,
        open_client(as_user="bob") as bob,
    ):
        _conversation_id, events = await ask_for_return(alice)
        action_id = proposed_action(events)["id"]

        responses = [
            await bob.get(f"/api/actions/{action_id}"),
            await bob.post(f"/api/actions/{action_id}/confirm"),
            await bob.post(f"/api/actions/{action_id}/cancel"),
        ]
        state = await alice.get(f"/api/actions/{action_id}")

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert state.json()["status"] == "pending"


async def test_confirm_from_another_site_is_rejected(open_client: OpenClient, earbuds_item: int) -> None:
    async with open_client(chat_model=proposing_model(earbuds_item)) as client:
        _conversation_id, events = await ask_for_return(client)
        action_id = proposed_action(events)["id"]

        response = await client.post(
            f"/api/actions/{action_id}/confirm", headers={"Origin": "http://evil.example.com"}
        )

    assert response.status_code == 403


async def test_offline_model_proposes_a_return_for_an_explicit_order_item(
    open_client: OpenClient, earbuds_item: int
) -> None:
    async with open_client() as client:
        conversation_id = await create_conversation(client)
        events = await send_message(client, conversation_id, f"退货 订单项 {earbuds_item}")

    action = proposed_action(events)
    assert (action["preview"]["order_item_id"], action["preview"]["quantity"]) == (earbuds_item, 1)
