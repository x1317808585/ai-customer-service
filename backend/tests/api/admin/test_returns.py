from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.returns import Returns, ReturnView
from app.models import Order, Product, User
from tests.api.chat_api import create_conversation, displayed_text, send_message

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

STOCK = 5


@pytest.fixture
async def pending_return(
    users: dict[str, User], add_product: AddProduct, add_order: AddOrder, session_factory: SessionFactory
) -> ReturnView:
    """alice 申请退 1 副耳机，还在处理中。"""
    product = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=STOCK)
    order = await add_order(users["alice"].id, product, delivered_at=datetime.now(UTC) - timedelta(days=1))
    return await Returns(session_factory).request_return(users["alice"].id, order.items[0].id, 1, "左耳没声音")


async def stock_of_earbuds(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(Product.stock).where(Product.name == "SoundPods 降噪耳机"))


async def test_staff_see_pending_returns_with_customer_and_delivery(
    open_client: OpenClient, pending_return: ReturnView
) -> None:
    async with open_client(as_user="admin") as admin:
        listed = (await admin.get("/api/admin/returns")).json()
        detail = (await admin.get(f"/api/admin/returns/{pending_return.id}")).json()

    assert [(r["id"], r["customer"], r["status"]) for r in listed] == [(pending_return.id, "alice", "pending")]
    assert (detail["product_name"], detail["refund_cents"], detail["reason"]) == ("SoundPods 降噪耳机", 59900, "左耳没声音")
    assert detail["delivered_at"] is not None


@pytest.mark.parametrize("body", [None, {"note": "已验收入库"}], ids=["without-note", "with-note"])
async def test_completing_restocks_and_records_the_logged_in_admin(
    open_client: OpenClient, pending_return: ReturnView, session_factory: SessionFactory, body: dict[str, Any] | None
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(f"/api/admin/returns/{pending_return.id}/complete", json=body)

    assert response.status_code == 200
    completed = response.json()
    assert (completed["status"], completed["resolved_by"]) == ("completed", "admin")
    assert completed["resolution_note"] == (body or {}).get("note")
    assert await stock_of_earbuds(session_factory) == STOCK + 1


async def test_rejecting_keeps_stock_and_customer_can_see_the_reason(
    open_client: OpenClient, pending_return: ReturnView, session_factory: SessionFactory
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(
            f"/api/admin/returns/{pending_return.id}/reject", json={"reason": "商品已使用，不符合退货条件"}
        )
    async with open_client(as_user="alice") as alice:
        conversation_id = await create_conversation(alice)
        reply = displayed_text(await send_message(alice, conversation_id, "我的退货"))

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["resolved_by"]) == ("rejected", "admin")
    assert await stock_of_earbuds(session_factory) == STOCK
    assert "已拒绝（说明：商品已使用，不符合退货条件）" in reply


async def test_return_cancelled_by_customer_first_returns_409_with_current_state(
    open_client: OpenClient, users: dict[str, User], pending_return: ReturnView, session_factory: SessionFactory
) -> None:
    await Returns(session_factory).cancel_return(users["alice"].id, pending_return.id)

    async with open_client(as_user="admin") as admin:
        response = await admin.post(f"/api/admin/returns/{pending_return.id}/complete", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "退货申请已经撤回，不能再处理"
    assert (response.json()["return"]["status"], response.json()["return"]["resolved_by"]) == ("cancelled", "alice")
    assert await stock_of_earbuds(session_factory) == STOCK


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("reject", {}),
        ("reject", {"reason": "   "}),
        ("reject", {"reason": "已使用", "resolved_by": 1}),
        ("complete", {"note": "长" * 501}),
        ("complete", {"admin_id": 1}),
    ],
    ids=["reject-no-reason", "reject-blank-reason", "reject-resolved-by", "complete-long-note", "complete-admin-id"],
)
async def test_bad_input_and_operator_fields_are_rejected(
    open_client: OpenClient,
    pending_return: ReturnView,
    session_factory: SessionFactory,
    action: str,
    body: dict[str, Any],
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(f"/api/admin/returns/{pending_return.id}/{action}", json=body)
        detail = (await admin.get(f"/api/admin/returns/{pending_return.id}")).json()

    assert response.status_code == 422
    assert detail["status"] == "pending"
    assert await stock_of_earbuds(session_factory) == STOCK


async def test_unknown_return_is_404(open_client: OpenClient) -> None:
    async with open_client(as_user="admin") as admin:
        responses = [
            await admin.get("/api/admin/returns/999"),
            await admin.post("/api/admin/returns/999/complete", json={}),
            await admin.post("/api/admin/returns/999/reject", json={"reason": "已使用"}),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404]
