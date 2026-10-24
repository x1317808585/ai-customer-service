from collections.abc import Awaitable, Callable
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Order, OrderItem, OrderStatus, Product

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]


@pytest.fixture
def add_product(session_factory: async_sessionmaker[AsyncSession]) -> AddProduct:
    """往测试库里加一个商品，返回它的 id。"""

    async def _add_product(
        name: str, price_cents: int = 1000, stock: int = 10, returnable: bool = True
    ) -> int:
        async with session_factory() as session:
            product = Product(
                name=name,
                description=f"{name}的商品说明",
                price_cents=price_cents,
                stock=stock,
                returnable=returnable,
            )
            session.add(product)
            await session.commit()
            return product.id

    return _add_product


@pytest.fixture
def add_order(session_factory: async_sessionmaker[AsyncSession]) -> AddOrder:
    """直接写入一个只有一项商品的订单（不扣库存），用来准备各种状态的订单。

    status 为 delivered 时必须同时给 delivered_at。返回的订单带着 items。
    """

    async def _add_order(
        user_id: int,
        product_id: int,
        quantity: int = 1,
        status: OrderStatus = "delivered",
        delivered_at: datetime | None = None,
    ) -> Order:
        async with session_factory() as session:
            product = await session.get_one(Product, product_id)
            item = OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price_cents=product.price_cents,
                quantity=quantity,
            )
            order = Order(
                user_id=user_id,
                status=status,
                total_cents=product.price_cents * quantity,
                delivered_at=delivered_at,
                items=[item],
            )
            session.add(order)
            await session.commit()
            return order

    return _add_order
