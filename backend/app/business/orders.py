from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.business.catalog import ProductNotFoundError
from app.models import Order, OrderItem, OrderStatus, Product

LIST_LIMIT = 20


class OrderNotFoundError(Exception):
    def __init__(self, order_id: int) -> None:
        super().__init__(f"订单 {order_id} 不存在")
        self.order_id = order_id


class OutOfStockError(Exception):
    def __init__(self, product_name: str, requested: int, available: int) -> None:
        super().__init__(f"「{product_name}」库存不足：需要 {requested} 件，只剩 {available} 件")
        self.product_name = product_name
        self.requested = requested
        self.available = available


class InvalidOrderError(Exception):
    """订单内容本身有问题，比如没有商品、同一商品出现两行。"""


class OrderLine(BaseModel):
    """下单时的一行：买哪个商品、买几件。价格不由调用方传，一律以数据库为准。"""

    model_config = ConfigDict(frozen=True)

    product_id: int
    quantity: PositiveInt


class OrderItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    product_id: int
    product_name: str
    unit_price_cents: int
    quantity: int


class OrderView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    status: OrderStatus
    total_cents: int
    created_at: datetime
    delivered_at: datetime | None
    items: list[OrderItemView]


class Orders:
    """订单：查看自己的订单、下单。每个方法都要传 user_id，只能操作自己的订单。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_orders(self, user_id: int) -> list[OrderView]:
        async with self._session_factory() as session:
            orders = await session.scalars(
                select(Order)
                .where(Order.user_id == user_id)
                .options(selectinload(Order.items))
                .order_by(Order.id.desc())
                .limit(LIST_LIMIT)
            )
            return [OrderView.model_validate(order) for order in orders]

    async def get_order(self, user_id: int, order_id: int) -> OrderView:
        async with self._session_factory() as session:
            order = await session.scalar(
                select(Order)
                .where(Order.id == order_id, Order.user_id == user_id)
                .options(selectinload(Order.items))
            )
            if order is None:
                raise OrderNotFoundError(order_id)
            return OrderView.model_validate(order)

    async def place_order(self, user_id: int, lines: Sequence[OrderLine]) -> OrderView:
        product_ids = [line.product_id for line in lines]
        if not product_ids:
            raise InvalidOrderError("订单里至少要有一件商品")
        if len(set(product_ids)) != len(product_ids):
            raise InvalidOrderError("同一个商品请合并成一行")

        async with self._session_factory() as session, session.begin():
            # 按 id 顺序给这些商品行加锁：别的下单请求要等这个事务结束才能读到库存，
            # 固定顺序则避免两个订单互相等对方的锁（死锁）。
            locked = await session.scalars(
                select(Product).where(Product.id.in_(product_ids)).order_by(Product.id).with_for_update()
            )
            products = {product.id: product for product in locked}

            order = Order(user_id=user_id, status="placed", total_cents=0)
            for line in lines:
                product = products.get(line.product_id)
                if product is None:
                    raise ProductNotFoundError(line.product_id)
                if product.stock < line.quantity:
                    raise OutOfStockError(product.name, line.quantity, product.stock)
                product.stock -= line.quantity
                order.items.append(
                    OrderItem(
                        product_id=product.id,
                        product_name=product.name,
                        unit_price_cents=product.price_cents,
                        quantity=line.quantity,
                    )
                )
            order.total_cents = sum(item.unit_price_cents * item.quantity for item in order.items)
            session.add(order)

        return OrderView.model_validate(order)
