from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.tickets import Tickets
from app.models import Order, User
from tests.api.chat_api import create_conversation, displayed_text, send_message

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def ticket_from_chat(
    open_client: OpenClient, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> dict[str, Any]:
    """alice 在聊天里转人工并确认，返回工单 id、来源会话 id 和订单 id。"""
    product = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, product, status="shipped")
    async with open_client(as_user="alice") as alice:
        conversation_id = await create_conversation(alice)
        events = await send_message(alice, conversation_id, f"转人工 订单 {order.id}，耳机左边没声音")
        [action] = [data["action"] for name, data in events if name == "action.proposed"]
        confirmed = (await alice.post(f"/api/actions/{action['id']}/confirm")).json()
    return {"id": confirmed["result"]["ticket_id"], "conversation_id": conversation_id, "order_id": order.id}


async def test_staff_see_open_tickets_and_the_source_conversation(
    open_client: OpenClient, ticket_from_chat: dict[str, Any]
) -> None:
    async with open_client(as_user="admin") as admin:
        listed = (await admin.get("/api/admin/tickets")).json()
        detail = (await admin.get(f"/api/admin/tickets/{ticket_from_chat['id']}")).json()

    assert [(t["id"], t["customer"], t["status"]) for t in listed] == [(ticket_from_chat["id"], "alice", "open")]
    assert detail["ticket"]["conversation_id"] == ticket_from_chat["conversation_id"]
    assert detail["ticket"]["order_id"] == ticket_from_chat["order_id"]
    transcript = detail["transcript"]
    assert [m["role"] for m in transcript] == ["user", "assistant", "assistant"]  # 提问、确认卡片、回复
    assert transcript[1]["action"]["kind"] == "ticket" and transcript[1]["action"]["status"] == "executed"


async def test_ticket_without_source_conversation_has_empty_transcript(
    open_client: OpenClient, users: dict[str, User], session_factory: SessionFactory
) -> None:
    ticket = await Tickets(session_factory).open_ticket(users["bob"].id, "想咨询企业采购")

    async with open_client(as_user="admin") as admin:
        detail = (await admin.get(f"/api/admin/tickets/{ticket.id}")).json()

    assert (detail["ticket"]["customer"], detail["transcript"]) == ("bob", [])


async def test_closing_records_the_logged_in_admin_and_customer_can_see_the_result(
    open_client: OpenClient, ticket_from_chat: dict[str, Any]
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(
            f"/api/admin/tickets/{ticket_from_chat['id']}/close", json={"resolution": "已联系物流补发"}
        )
        closed_list = (await admin.get("/api/admin/tickets", params={"status": "closed"})).json()
    async with open_client(as_user="alice") as alice:
        conversation_id = await create_conversation(alice)
        reply = displayed_text(await send_message(alice, conversation_id, "我的工单"))

    assert response.status_code == 200
    closed = response.json()
    assert (closed["status"], closed["resolution"], closed["resolved_by"]) == ("closed", "已联系物流补发", "admin")
    assert [t["id"] for t in closed_list] == [ticket_from_chat["id"]]
    assert "已处理（处理结果：已联系物流补发）" in reply


async def test_closing_twice_returns_409_with_the_current_ticket(
    open_client: OpenClient, ticket_from_chat: dict[str, Any]
) -> None:
    path = f"/api/admin/tickets/{ticket_from_chat['id']}/close"
    async with open_client(as_user="admin") as admin:
        await admin.post(path, json={"resolution": "已联系物流补发"})
        again = await admin.post(path, json={"resolution": "再处理一次"})

    assert again.status_code == 409
    assert again.json()["detail"] == "工单已经关闭，不能再处理"
    assert again.json()["ticket"]["resolution"] == "已联系物流补发"


@pytest.mark.parametrize(
    "body",
    [{"resolution": "   "}, {}, {"resolution": "已处理", "resolved_by": 1}, {"resolution": "已处理", "admin_id": 1}],
    ids=["blank", "missing", "resolved-by", "admin-id"],
)
async def test_close_rejects_bad_input_and_operator_fields(
    open_client: OpenClient, ticket_from_chat: dict[str, Any], body: dict[str, Any]
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(f"/api/admin/tickets/{ticket_from_chat['id']}/close", json=body)
        detail = (await admin.get(f"/api/admin/tickets/{ticket_from_chat['id']}")).json()

    assert response.status_code == 422
    assert detail["ticket"]["status"] == "open"


async def test_unknown_ticket_is_404(open_client: OpenClient) -> None:
    async with open_client(as_user="admin") as admin:
        responses = [
            await admin.get("/api/admin/tickets/999"),
            await admin.post("/api/admin/tickets/999/close", json={"resolution": "已处理"}),
        ]

    assert [response.status_code for response in responses] == [404, 404]
