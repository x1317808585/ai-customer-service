import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.returns import (
    RETURN_WINDOW,
    InvalidReturnError,
    OrderItemNotFoundError,
    OrderNotDeliveredError,
    ProductNotReturnableError,
    ReturnNotFoundError,
    ReturnNotPendingError,
    ReturnQuantityExceededError,
    Returns,
    ReturnWindowExpiredError,
)
from app.models import Order, OrderItem, Product, ReturnRequest, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

DELIVERED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def returns_at(session_factory: SessionFactory, moment: datetime) -> Returns:
    """创建一个"现在"固定为 moment 的 Returns，这样退货期的边界可以精确测试。"""
    return Returns(session_factory, clock=lambda: moment)


@pytest.fixture
def returns(session_factory: SessionFactory) -> Returns:
    return returns_at(session_factory, DELIVERED_AT + timedelta(days=1))


@pytest.fixture
async def earbuds(add_product: AddProduct) -> int:
    return await add_product("SoundPods 降噪耳机", price_cents=59900, stock=5)


async def delivered_item(add_order: AddOrder, user: User, product_id: int, quantity: int = 1) -> int:
    """准备一个已签收的订单，返回其中订单项的 id。"""
    order = await add_order(user.id, product_id, quantity=quantity, delivered_at=DELIVERED_AT)
    return order.items[0].id


async def stock_of(session_factory: SessionFactory, product_id: int) -> int:
    async with session_factory() as session:
        return await session.scalar(select(Product.stock).where(Product.id == product_id))


async def return_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(ReturnRequest))


# ---------- 申请退货 ----------


async def test_request_return_snapshots_refund_and_waits_for_review(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=2)

    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="  左耳没声音  ")

    assert (request.status, request.product_name, request.quantity, request.refund_cents, request.reason) == (
        "pending",
        "SoundPods 降噪耳机",
        1,
        59900,
        "左耳没声音",
    )
    assert request.resolved_at is None
    assert await stock_of(session_factory, earbuds) == 5  # 退货完成前不入库


async def test_pending_and_completed_returns_both_use_up_returnable_quantity(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=3)
    completed = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    await returns.complete_return(users["admin"].id, completed.id)
    await returns.request_return(users["alice"].id, item_id, quantity=1, reason="不想要了")

    with pytest.raises(ReturnQuantityExceededError) as error:
        await returns.request_return(users["alice"].id, item_id, quantity=2, reason="都退了")

    assert (error.value.requested, error.value.remaining) == (2, 1)


async def test_rejected_and_cancelled_returns_free_up_quantity(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=1)

    rejected = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    await returns.reject_return(users["admin"].id, rejected.id, "商品已使用")
    cancelled = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    await returns.cancel_return(users["alice"].id, cancelled.id)
    again = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    assert again.status == "pending"


@pytest.mark.parametrize(
    ("since_delivery", "allowed"),
    [(RETURN_WINDOW, True), (RETURN_WINDOW + timedelta(seconds=1), False)],
    ids=["last-moment", "one-second-late"],
)
async def test_return_window_counts_from_delivery(
    users: dict[str, User],
    add_order: AddOrder,
    earbuds: int,
    session_factory: SessionFactory,
    since_delivery: timedelta,
    allowed: bool,
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)
    returns = returns_at(session_factory, DELIVERED_AT + since_delivery)

    if allowed:
        await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    else:
        with pytest.raises(ReturnWindowExpiredError):
            await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")


async def test_return_window_can_be_configured(
    users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)
    returns = Returns(
        session_factory, return_window=timedelta(days=15), clock=lambda: DELIVERED_AT + timedelta(days=10)
    )

    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    assert request.status == "pending"


@pytest.mark.parametrize("status", ["placed", "shipped", "cancelled"])
async def test_undelivered_orders_cannot_be_returned(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, status: str
) -> None:
    order = await add_order(users["alice"].id, earbuds, status=status)

    with pytest.raises(OrderNotDeliveredError):
        await returns.request_return(users["alice"].id, order.items[0].id, quantity=1, reason="坏了")


async def test_non_returnable_product_cannot_be_returned(
    returns: Returns, users: dict[str, User], add_order: AddOrder, add_product: AddProduct
) -> None:
    cup = await add_product("定制刻字保温杯", returnable=False)
    item_id = await delivered_item(add_order, users["alice"], cup)

    with pytest.raises(ProductNotReturnableError):
        await returns.request_return(users["alice"].id, item_id, quantity=1, reason="不喜欢")


async def test_cannot_return_someone_elses_item(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)

    with pytest.raises(OrderItemNotFoundError):
        await returns.request_return(users["bob"].id, item_id, quantity=1, reason="坏了")
    with pytest.raises(OrderItemNotFoundError):
        await returns.request_return(users["alice"].id, 999, quantity=1, reason="坏了")
    assert await return_count(session_factory) == 0


@pytest.mark.parametrize(
    ("quantity", "reason"),
    [(0, "坏了"), (-1, "坏了"), (1, "   "), (1, "长" * 501)],
    ids=["zero", "negative", "blank-reason", "reason-too-long"],
)
async def test_invalid_return_requests_are_rejected(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, quantity: int, reason: str
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)

    with pytest.raises(InvalidReturnError):
        await returns.request_return(users["alice"].id, item_id, quantity=quantity, reason=reason)


async def test_concurrent_requests_never_exceed_purchased_quantity(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=1)

    # 和下单的并发测试一样：先锁住这个订单项，让 10 个申请堵在同一起跑线上再一起放行。
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(OrderItem).where(OrderItem.id == item_id).with_for_update())
        attempts = [
            asyncio.create_task(returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了"))
            for _ in range(10)
        ]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts, return_exceptions=True)

    succeeded = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(succeeded) == 1
    assert all(isinstance(failure, ReturnQuantityExceededError) for failure in failures)
    assert await return_count(session_factory) == 1


# ---------- 处理退货 ----------


async def test_completing_a_return_restocks_exactly_once(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=2)
    request = await returns.request_return(users["alice"].id, item_id, quantity=2, reason="坏了")

    completed = await returns.complete_return(users["admin"].id, request.id, note=" 已验收入库 ")
    with pytest.raises(ReturnNotPendingError):
        await returns.complete_return(users["admin"].id, request.id)

    assert (completed.status, completed.resolved_by, completed.resolution_note) == ("completed", "admin", "已验收入库")
    assert completed.resolved_at is not None
    assert await stock_of(session_factory, earbuds) == 5 + 2


async def test_concurrent_completions_restock_only_once(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=1)
    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(ReturnRequest).where(ReturnRequest.id == request.id).with_for_update())
        attempts = [asyncio.create_task(returns.complete_return(users["admin"].id, request.id)) for _ in range(5)]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts, return_exceptions=True)

    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 4
    assert all(isinstance(failure, ReturnNotPendingError) for failure in failures)
    assert await stock_of(session_factory, earbuds) == 5 + 1


async def test_rejecting_a_return_does_not_restock(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, session_factory: SessionFactory
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)
    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    rejected = await returns.reject_return(users["admin"].id, request.id, " 商品已使用，不符合退货条件 ")

    assert (rejected.status, rejected.resolved_by) == ("rejected", "admin")
    assert rejected.resolution_note == "商品已使用，不符合退货条件"
    assert await stock_of(session_factory, earbuds) == 5


@pytest.mark.parametrize("reason", ["", "   ", "长" * 501], ids=["empty", "blank", "too-long"])
async def test_rejecting_requires_a_short_reason(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int, reason: str
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)
    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    with pytest.raises(InvalidReturnError):
        await returns.reject_return(users["admin"].id, request.id, reason)

    assert (await returns.get_for_staff(request.id)).status == "pending"


async def test_customer_cancelling_is_recorded_as_resolved_by_the_customer(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)
    request = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    await returns.cancel_return(users["alice"].id, request.id)

    staff_view = await returns.get_for_staff(request.id)
    assert (staff_view.status, staff_view.resolved_by, staff_view.resolution_note) == ("cancelled", "alice", None)


async def test_staff_see_returns_of_every_customer_by_status(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    alice_item = await delivered_item(add_order, users["alice"], earbuds)
    bob_item = await delivered_item(add_order, users["bob"], earbuds)
    alice_request = await returns.request_return(users["alice"].id, alice_item, quantity=1, reason="坏了")
    bob_request = await returns.request_return(users["bob"].id, bob_item, quantity=1, reason="不想要了")
    await returns.complete_return(users["admin"].id, alice_request.id)

    pending = await returns.list_for_staff("pending")
    completed = await returns.list_for_staff("completed")

    assert [(r.id, r.customer) for r in pending] == [(bob_request.id, "bob")]
    assert [(r.id, r.customer, r.resolved_by) for r in completed] == [(alice_request.id, "alice", "admin")]
    assert pending[0].delivered_at is not None


async def test_only_the_owner_can_cancel_and_only_while_pending(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=2)
    first = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    second = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    await returns.complete_return(users["admin"].id, second.id)

    with pytest.raises(ReturnNotFoundError):
        await returns.cancel_return(users["bob"].id, first.id)
    with pytest.raises(ReturnNotPendingError):
        await returns.cancel_return(users["alice"].id, second.id)
    cancelled = await returns.cancel_return(users["alice"].id, first.id)

    assert cancelled.status == "cancelled"


async def test_unknown_return_is_not_found(returns: Returns, users: dict[str, User]) -> None:
    with pytest.raises(ReturnNotFoundError):
        await returns.complete_return(users["admin"].id, 999)
    with pytest.raises(ReturnNotFoundError):
        await returns.get_for_staff(999)


# ---------- 查询 ----------


async def test_users_only_see_their_own_returns_newest_first(
    returns: Returns, users: dict[str, User], add_order: AddOrder, earbuds: int
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds, quantity=2)
    first = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
    second = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    alice_returns = await returns.list_returns(users["alice"].id)

    assert [request.id for request in alice_returns] == [second.id, first.id]
    assert await returns.list_returns(users["bob"].id) == []


# ---------- 数据库约束 ----------


@pytest.mark.parametrize(
    ("status", "resolved_at"),
    [("pending", DELIVERED_AT), ("completed", None)],
    ids=["pending-with-time", "completed-without-time"],
)
async def test_resolved_at_is_set_exactly_for_finished_returns(
    users: dict[str, User],
    add_order: AddOrder,
    earbuds: int,
    session_factory: SessionFactory,
    status: str,
    resolved_at: datetime | None,
) -> None:
    item_id = await delivered_item(add_order, users["alice"], earbuds)

    async with session_factory() as session:
        session.add(
            ReturnRequest(
                order_item_id=item_id,
                quantity=1,
                refund_cents=0,
                reason="坏了",
                status=status,
                resolved_at=resolved_at,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
