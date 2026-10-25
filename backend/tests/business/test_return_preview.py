from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.business.returns import (
    RETURN_WINDOW,
    ReturnPreview,
    ReturnPreviewChangedError,
    Returns,
    ReturnWindowExpiredError,
)
from app.models import Order, ReturnRequest, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

DELIVERED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def returns_at(session_factory: SessionFactory, moment: datetime) -> Returns:
    return Returns(session_factory, clock=lambda: moment)


async def return_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(ReturnRequest))


@pytest.fixture
def returns(session_factory: SessionFactory) -> Returns:
    return returns_at(session_factory, DELIVERED_AT + timedelta(days=1))


# ---------- 资格查询、预览、正式提交的判断一致 ----------


@pytest.mark.parametrize(
    ("scenario", "eligible"),
    [("ok", True), ("expired", False), ("not-delivered", False), ("not-returnable", False), ("used-up", False)],
)
async def test_eligibility_preview_and_request_agree(
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
    scenario: str,
    eligible: bool,
) -> None:
    product = await add_product("SoundPods 降噪耳机", price_cents=59900, returnable=scenario != "not-returnable")
    if scenario == "not-delivered":
        order = await add_order(users["alice"].id, product, status="shipped")
    else:
        order = await add_order(users["alice"].id, product, delivered_at=DELIVERED_AT)
    item_id = order.items[0].id
    now = DELIVERED_AT + (RETURN_WINDOW + timedelta(days=1) if scenario == "expired" else timedelta(days=1))
    returns = returns_at(session_factory, now)
    if scenario == "used-up":
        await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")

    eligibility = await returns.check_eligibility(users["alice"].id, item_id)

    assert eligibility.eligible is eligible
    if eligible:
        preview = await returns.preview_return(users["alice"].id, item_id, quantity=1, reason="坏了")
        created = await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")
        assert (preview.product_name, preview.quantity, preview.refund_cents) == (
            created.product_name,
            created.quantity,
            created.refund_cents,
        )
    else:
        assert eligibility.reason
        with pytest.raises(BusinessError, match=eligibility.reason):
            await returns.preview_return(users["alice"].id, item_id, quantity=1, reason="坏了")
        with pytest.raises(BusinessError, match=eligibility.reason):
            await returns.request_return(users["alice"].id, item_id, quantity=1, reason="坏了")


async def test_eligibility_tells_remaining_quantity_and_deadline(
    returns: Returns, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    product = await add_product("USB-C 快充线")
    order = await add_order(users["alice"].id, product, quantity=3, delivered_at=DELIVERED_AT)
    await returns.request_return(users["alice"].id, order.items[0].id, quantity=1, reason="多买了")

    eligibility = await returns.check_eligibility(users["alice"].id, order.items[0].id)

    assert (eligibility.eligible, eligibility.returnable_quantity) == (True, 2)
    assert eligibility.return_deadline == DELIVERED_AT + RETURN_WINDOW


async def test_preview_writes_nothing(
    returns: Returns,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    product = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, product, quantity=2, delivered_at=DELIVERED_AT)

    preview = await returns.preview_return(users["alice"].id, order.items[0].id, quantity=2, reason=" 左耳没声音 ")

    assert preview == ReturnPreview(
        order_id=order.id,
        order_item_id=order.items[0].id,
        product_name="SoundPods 降噪耳机",
        quantity=2,
        refund_cents=119800,
        reason="左耳没声音",
    )
    assert await return_count(session_factory) == 0


# ---------- 在调用方的事务里提交 ----------


async def test_request_in_callers_transaction_is_undone_when_caller_rolls_back(
    returns: Returns,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    product = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, product, delivered_at=DELIVERED_AT)

    async with session_factory() as session:
        await session.begin()
        created = await returns.request_return_in(session, users["alice"].id, order.items[0].id, 1, "坏了")
        assert created.id is not None
        await session.rollback()

    assert await return_count(session_factory) == 0


async def test_request_is_refused_when_it_no_longer_matches_the_confirmed_preview(
    returns: Returns,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    product = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, product, delivered_at=DELIVERED_AT)
    confirmed = await returns.preview_return(users["alice"].id, order.items[0].id, quantity=1, reason="坏了")
    tampered = confirmed.model_copy(update={"refund_cents": 1})

    async with session_factory() as session, session.begin():
        with pytest.raises(ReturnPreviewChangedError):
            await returns.request_return_in(
                session, users["alice"].id, order.items[0].id, 1, "坏了", expected=tampered
            )

    assert await return_count(session_factory) == 0


async def test_window_closing_after_preview_is_caught_on_submit(
    users: dict[str, User], add_product: AddProduct, add_order: AddOrder, session_factory: SessionFactory
) -> None:
    product = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, product, delivered_at=DELIVERED_AT)
    before = returns_at(session_factory, DELIVERED_AT + RETURN_WINDOW)
    after = returns_at(session_factory, DELIVERED_AT + RETURN_WINDOW + timedelta(seconds=1))
    confirmed = await before.preview_return(users["alice"].id, order.items[0].id, quantity=1, reason="坏了")

    async with session_factory() as session, session.begin():
        with pytest.raises(ReturnWindowExpiredError):
            await after.request_return_in(session, users["alice"].id, order.items[0].id, 1, "坏了", expected=confirmed)
