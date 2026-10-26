import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.returns import (
    ReturnCancellationPreview,
    ReturnNotFoundError,
    ReturnNotPendingError,
    Returns,
    ReturnView,
)
from app.models import Order, Product, ReturnRequest, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

DELIVERED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
STOCK = 5


@pytest.fixture
def returns(session_factory: SessionFactory) -> Returns:
    return Returns(session_factory, clock=lambda: DELIVERED_AT + timedelta(days=1))


@pytest.fixture
async def earbuds_return(
    returns: Returns, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> ReturnView:
    """alice 买了 2 副耳机，申请退 1 副，申请还在处理中。"""
    product = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=STOCK)
    order = await add_order(users["alice"].id, product, quantity=2, delivered_at=DELIVERED_AT)
    return await returns.request_return(users["alice"].id, order.items[0].id, quantity=1, reason="坏了")


async def stock_of(session_factory: SessionFactory, product_name: str) -> int:
    async with session_factory() as session:
        return await session.scalar(select(Product.stock).where(Product.name == product_name))


async def status_of(session_factory: SessionFactory, return_id: int) -> str:
    async with session_factory() as session:
        return (await session.get_one(ReturnRequest, return_id)).status


# ---------- 预览 ----------


async def test_preview_shows_what_will_be_cancelled(
    returns: Returns, users: dict[str, User], earbuds_return: ReturnView, session_factory: SessionFactory
) -> None:
    preview = await returns.preview_cancellation(users["alice"].id, earbuds_return.id)

    assert preview == ReturnCancellationPreview(
        return_id=earbuds_return.id,
        order_id=earbuds_return.order_id,
        order_item_id=earbuds_return.order_item_id,
        product_name="SoundPods 降噪耳机",
        quantity=1,
        refund_cents=59900,
    )
    assert await status_of(session_factory, earbuds_return.id) == "pending"  # 预览不写入


async def test_someone_elses_return_is_not_found(
    returns: Returns, users: dict[str, User], earbuds_return: ReturnView, session_factory: SessionFactory
) -> None:
    with pytest.raises(ReturnNotFoundError):
        await returns.preview_cancellation(users["bob"].id, earbuds_return.id)
    async with session_factory() as session, session.begin():
        with pytest.raises(ReturnNotFoundError):
            await returns.cancel_return_in(session, users["bob"].id, earbuds_return.id)

    assert await status_of(session_factory, earbuds_return.id) == "pending"


@pytest.mark.parametrize(
    ("finish", "message"),
    [
        ("complete_return", "退货申请已经完成，不能再处理"),
        ("reject_return", "退货申请已经被拒绝，不能再处理"),
        ("cancel_return", "退货申请已经撤回，不能再处理"),
    ],
    ids=["completed", "rejected", "cancelled"],
)
async def test_finished_return_cannot_be_cancelled_and_says_why_in_chinese(
    returns: Returns,
    users: dict[str, User],
    earbuds_return: ReturnView,
    session_factory: SessionFactory,
    finish: str,
    message: str,
) -> None:
    if finish == "cancel_return":
        await returns.cancel_return(users["alice"].id, earbuds_return.id)
    elif finish == "complete_return":
        await returns.complete_return(users["admin"].id, earbuds_return.id)
    else:
        await returns.reject_return(users["admin"].id, earbuds_return.id, "商品已使用")

    with pytest.raises(ReturnNotPendingError, match=message):
        await returns.preview_cancellation(users["alice"].id, earbuds_return.id)
    async with session_factory() as session, session.begin():
        with pytest.raises(ReturnNotPendingError, match=message):
            await returns.cancel_return_in(session, users["alice"].id, earbuds_return.id)


# ---------- 在调用方的事务里撤回 ----------


async def test_cancel_keeps_stock_and_frees_the_quantity_for_a_new_request(
    returns: Returns, users: dict[str, User], earbuds_return: ReturnView, session_factory: SessionFactory
) -> None:
    async with session_factory() as session, session.begin():
        cancelled = await returns.cancel_return_in(session, users["alice"].id, earbuds_return.id)

    eligibility = await returns.check_eligibility(users["alice"].id, earbuds_return.order_item_id)

    assert cancelled.status == "cancelled" and cancelled.resolved_at is not None
    assert await stock_of(session_factory, "SoundPods 降噪耳机") == STOCK  # 只有完成退货才放回库存
    assert eligibility.returnable_quantity == 2  # 撤回的那 1 副不再占用可退数量


async def test_cancel_in_callers_transaction_is_undone_when_caller_rolls_back(
    returns: Returns, users: dict[str, User], earbuds_return: ReturnView, session_factory: SessionFactory
) -> None:
    async with session_factory() as session:
        await session.begin()
        await returns.cancel_return_in(session, users["alice"].id, earbuds_return.id)
        await session.rollback()

    assert await status_of(session_factory, earbuds_return.id) == "pending"


async def test_cancel_and_staff_completion_at_the_same_time_only_one_wins(
    returns: Returns, users: dict[str, User], earbuds_return: ReturnView, session_factory: SessionFactory
) -> None:
    async def cancel() -> ReturnView:
        async with session_factory() as session, session.begin():
            return await returns.cancel_return_in(session, users["alice"].id, earbuds_return.id)

    # 先锁住这条申请，让顾客撤回和客服完成堵在同一起跑线上再一起放行
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(ReturnRequest).where(ReturnRequest.id == earbuds_return.id).with_for_update())
        attempts = [
            asyncio.create_task(cancel()),
            asyncio.create_task(returns.complete_return(users["admin"].id, earbuds_return.id)),
        ]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts, return_exceptions=True)

    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 1 and isinstance(failures[0], ReturnNotPendingError)
    final = await status_of(session_factory, earbuds_return.id)
    expected_stock = STOCK + 1 if final == "completed" else STOCK
    assert await stock_of(session_factory, "SoundPods 降噪耳机") == expected_stock
