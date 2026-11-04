import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.catalog import ProductNotFoundError
from app.business.orders import (
    InvalidOrderError,
    OrderLine,
    OrderNotFoundError,
    OrderPriceChangedError,
    Orders,
    OutOfStockError,
)
from app.models import Order, Product, User

AddProduct = Callable[..., Awaitable[int]]
SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def orders(session_factory: SessionFactory) -> Orders:
    return Orders(session_factory)


async def stock_of(session_factory: SessionFactory, product_id: int) -> int:
    async with session_factory() as session:
        return await session.scalar(select(Product.stock).where(Product.id == product_id))


async def order_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Order))


# ---------- 下单 ----------


async def test_place_order_uses_server_prices_and_deducts_stock(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=5)
    cable = await add_product("USB-C 充电线", price_cents=3900, stock=5)

    order = await orders.place_order(
        users["alice"].id, [OrderLine(product_id=earbuds, quantity=1), OrderLine(product_id=cable, quantity=2)]
    )

    assert order.status == "placed"
    assert order.total_cents == 59900 + 3900 * 2
    assert [(item.product_name, item.unit_price_cents, item.quantity) for item in order.items] == [
        ("SoundPods 降噪耳机", 59900, 1),
        ("USB-C 充电线", 3900, 2),
    ]
    assert await stock_of(session_factory, earbuds) == 4
    assert await stock_of(session_factory, cable) == 3


async def test_order_keeps_name_and_price_snapshot_after_product_changes(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900)
    placed = await orders.place_order(users["alice"].id, [OrderLine(product_id=earbuds, quantity=1)])

    async with session_factory() as session:
        await session.execute(
            update(Product).where(Product.id == earbuds).values(name="SoundPods 2", price_cents=69900)
        )
        await session.commit()
    order = await orders.get_order(users["alice"].id, placed.id)

    assert (order.items[0].product_name, order.items[0].unit_price_cents, order.total_cents) == (
        "SoundPods 降噪耳机",
        59900,
        59900,
    )


async def test_out_of_stock_order_writes_nothing(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", stock=1)

    with pytest.raises(OutOfStockError) as error:
        await orders.place_order(users["alice"].id, [OrderLine(product_id=earbuds, quantity=2)])

    assert (error.value.requested, error.value.available) == (2, 1)
    assert await stock_of(session_factory, earbuds) == 1
    assert await order_count(session_factory) == 0


async def test_multi_item_order_rolls_back_entirely_when_one_item_is_short(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    cable = await add_product("USB-C 充电线", stock=10)
    earbuds = await add_product("SoundPods 降噪耳机", stock=0)

    with pytest.raises(OutOfStockError):
        await orders.place_order(
            users["alice"].id,
            [OrderLine(product_id=cable, quantity=1), OrderLine(product_id=earbuds, quantity=1)],
        )

    assert await stock_of(session_factory, cable) == 10
    assert await order_count(session_factory) == 0


async def test_concurrent_orders_never_oversell(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", stock=1)
    buyers = [users["alice"].id, users["bob"].id] * 5

    # 测试先锁住这件商品，让 10 个下单请求都堵在同一起跑线上，再一起放行。
    # 不这样做的话，各请求建立数据库连接的耗时会把它们错开，冲突就时有时无。
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(Product).where(Product.id == earbuds).with_for_update())
        attempts = [
            asyncio.create_task(orders.place_order(buyer, [OrderLine(product_id=earbuds, quantity=1)]))
            for buyer in buyers
        ]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts, return_exceptions=True)

    succeeded = [result for result in results if not isinstance(result, BaseException)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(succeeded) == 1
    assert all(isinstance(failure, OutOfStockError) for failure in failures)
    assert await stock_of(session_factory, earbuds) == 0
    assert await order_count(session_factory) == 1


async def test_unknown_product_order_writes_nothing(
    orders: Orders, users: dict[str, User], session_factory: SessionFactory
) -> None:
    with pytest.raises(ProductNotFoundError):
        await orders.place_order(users["alice"].id, [OrderLine(product_id=999, quantity=1)])

    assert await order_count(session_factory) == 0


async def test_empty_or_duplicate_lines_are_invalid(
    orders: Orders, users: dict[str, User], add_product: AddProduct
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")

    with pytest.raises(InvalidOrderError):
        await orders.place_order(users["alice"].id, [])
    with pytest.raises(InvalidOrderError):
        await orders.place_order(
            users["alice"].id,
            [OrderLine(product_id=earbuds, quantity=1), OrderLine(product_id=earbuds, quantity=2)],
        )


@pytest.mark.parametrize("quantity", [0, -1])
def test_quantity_must_be_positive(quantity: int) -> None:
    with pytest.raises(ValidationError):
        OrderLine(product_id=1, quantity=quantity)


# ---------- 查询 ----------


async def test_users_only_see_their_own_orders(
    orders: Orders, users: dict[str, User], add_product: AddProduct
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    alice_order = await orders.place_order(users["alice"].id, [OrderLine(product_id=earbuds, quantity=1)])

    alice_orders = await orders.list_orders(users["alice"].id)
    bob_orders = await orders.list_orders(users["bob"].id)

    assert [order.id for order in alice_orders] == [alice_order.id]
    assert bob_orders == []
    with pytest.raises(OrderNotFoundError):
        await orders.get_order(users["bob"].id, alice_order.id)
    with pytest.raises(OrderNotFoundError):
        await orders.get_order(users["alice"].id, 999)


async def test_orders_are_listed_newest_first(
    orders: Orders, users: dict[str, User], add_product: AddProduct
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    first = await orders.place_order(users["alice"].id, [OrderLine(product_id=earbuds, quantity=1)])
    second = await orders.place_order(users["alice"].id, [OrderLine(product_id=earbuds, quantity=1)])

    listed = await orders.list_orders(users["alice"].id)

    assert [order.id for order in listed] == [second.id, first.id]


# ---------- 数据库约束 ----------


@pytest.mark.parametrize(
    ("status", "delivered_at"),
    [("delivered", None), ("placed", datetime(2026, 10, 1, tzinfo=UTC))],
    ids=["delivered-without-time", "not-delivered-with-time"],
)
async def test_delivered_at_is_set_exactly_for_delivered_orders(
    users: dict[str, User], session_factory: SessionFactory, status: str, delivered_at: datetime | None
) -> None:
    async with session_factory() as session:
        session.add(Order(user_id=users["alice"].id, status=status, total_cents=0, delivered_at=delivered_at))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_database_refuses_negative_stock(
    add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", stock=0)

    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(update(Product).where(Product.id == earbuds).values(stock=-1))


# ---------- 预览和按确认的报价下单 ----------


async def test_preview_quotes_server_prices_without_touching_stock(
    orders: Orders, add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=5)
    cable = await add_product("USB-C 充电线", price_cents=3900, stock=5)

    preview = await orders.preview_order(
        [OrderLine(product_id=earbuds, quantity=1), OrderLine(product_id=cable, quantity=2)]
    )

    assert [(line.product_name, line.unit_price_cents, line.quantity) for line in preview.lines] == [
        ("SoundPods 降噪耳机", 59900, 1),
        ("USB-C 充电线", 3900, 2),
    ]
    assert preview.total_cents == 67700
    assert await stock_of(session_factory, earbuds) == 5
    assert await order_count(session_factory) == 0


async def test_order_size_limits_apply_to_preview_and_order(
    orders: Orders, users: dict[str, User], add_product: AddProduct
) -> None:
    products = [await add_product(f"商品 {index}") for index in range(11)]
    too_many_kinds = [OrderLine(product_id=product, quantity=1) for product in products]
    too_many_pieces = [OrderLine(product_id=products[0], quantity=100)]

    for lines in (too_many_kinds, too_many_pieces):
        with pytest.raises(InvalidOrderError):
            await orders.preview_order(lines)
        with pytest.raises(InvalidOrderError):
            await orders.place_order(users["alice"].id, lines)


async def test_confirmed_order_is_refused_when_price_changed(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=5)
    lines = [OrderLine(product_id=earbuds, quantity=1)]
    confirmed = await orders.preview_order(lines)
    async with session_factory() as session:
        await session.execute(update(Product).where(Product.id == earbuds).values(price_cents=64900))
        await session.commit()

    with pytest.raises(OrderPriceChangedError) as error:
        async with session_factory() as session, session.begin():
            await orders.place_order_in(session, users["alice"].id, lines, confirmed=confirmed)

    assert str(error.value) == "「SoundPods 降噪耳机」的价格已从 599.00 元变为 649.00 元，请重新下单"
    assert await stock_of(session_factory, earbuds) == 5
    assert await order_count(session_factory) == 0


async def test_confirmed_order_goes_through_when_only_stock_changed(
    orders: Orders, users: dict[str, User], add_product: AddProduct, session_factory: SessionFactory
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=10)
    lines = [OrderLine(product_id=earbuds, quantity=1)]
    confirmed = await orders.preview_order(lines)
    async with session_factory() as session:
        await session.execute(update(Product).where(Product.id == earbuds).values(stock=9))
        await session.commit()

    async with session_factory() as session, session.begin():
        order = await orders.place_order_in(session, users["alice"].id, lines, confirmed=confirmed)

    assert (order.total_cents, order.items[0].unit_price_cents) == (59900, 59900)
    assert await stock_of(session_factory, earbuds) == 8
