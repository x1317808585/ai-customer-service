from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.business.catalog import ProductNotFoundError
from app.business.errors import BusinessError
from app.business.money import format_cents
from app.models import Order, OrderItem, OrderStatus, Product

LIST_LIMIT = 20
# 一个订单最多几种商品、每种最多几件。预览和下单用同一套检查
MAX_ORDER_LINES = 10
MAX_LINE_QUANTITY = 99


class OrderNotFoundError(BusinessError):
    def __init__(self, order_id: int) -> None:
        super().__init__(f"订单 {order_id} 不存在")
        self.order_id = order_id


class OutOfStockError(BusinessError):
    def __init__(self, product_name: str, requested: int, available: int) -> None:
        super().__init__(f"「{product_name}」库存不足：需要 {requested} 件，只剩 {available} 件")
        self.product_name = product_name
        self.requested = requested
        self.available = available


class InvalidOrderError(BusinessError):
    """订单内容本身有问题，比如没有商品、同一商品出现两行、数量超过上限。"""


class OrderPriceChangedError(BusinessError):
    """用户确认的报价和商品现在的价格不一样。不按新价格悄悄成交，请用户重新下单。"""

    def __init__(self, product_name: str, confirmed_cents: int, current_cents: int) -> None:
        super().__init__(
            f"「{product_name}」的价格已从 {format_cents(confirmed_cents)} 元变为 "
            f"{format_cents(current_cents)} 元，请重新下单"
        )


class OrderPreviewChangedError(BusinessError):
    """要下单的商品或数量和用户确认的不一样（正常流程不会出现）。"""

    def __init__(self) -> None:
        super().__init__("下单内容和确认时不一致，请重新下单")


class OrderLine(BaseModel):
    """下单时的一行：买哪个商品、买几件。价格不由调用方传，一律以数据库为准。"""

    model_config = ConfigDict(frozen=True)

    product_id: int
    quantity: PositiveInt


class OrderPreviewLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    product_id: int
    product_name: str
    unit_price_cents: int
    quantity: int


class OrderPreview(BaseModel):
    """将要创建的订单：用户确认的就是这些商品、数量和单价。不含库存，库存只在确认时检查够不够。"""

    model_config = ConfigDict(frozen=True)

    lines: list[OrderPreviewLine]
    total_cents: int


class OrderItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
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


def _check_lines(lines: Sequence[OrderLine]) -> None:
    product_ids = [line.product_id for line in lines]
    if not product_ids:
        raise InvalidOrderError("订单里至少要有一件商品")
    if len(product_ids) > MAX_ORDER_LINES:
        raise InvalidOrderError(f"一个订单最多 {MAX_ORDER_LINES} 种商品")
    if len(set(product_ids)) != len(product_ids):
        raise InvalidOrderError("同一个商品请合并成一行")
    if any(line.quantity > MAX_LINE_QUANTITY for line in lines):
        raise InvalidOrderError(f"每种商品一次最多买 {MAX_LINE_QUANTITY} 件")


def _price(products: Mapping[int, Product], lines: Sequence[OrderLine]) -> OrderPreview:
    """按数据库里的价格和库存给这些行报价。商品不存在、库存不够时拒绝。"""
    priced: list[OrderPreviewLine] = []
    for line in lines:
        product = products.get(line.product_id)
        if product is None:
            raise ProductNotFoundError(line.product_id)
        if product.stock < line.quantity:
            raise OutOfStockError(product.name, line.quantity, product.stock)
        priced.append(
            OrderPreviewLine(
                product_id=product.id,
                product_name=product.name,
                unit_price_cents=product.price_cents,
                quantity=line.quantity,
            )
        )
    return OrderPreview(lines=priced, total_cents=sum(line.unit_price_cents * line.quantity for line in priced))


def _ensure_same_prices(current: OrderPreview, confirmed: OrderPreview) -> None:
    """逐项比对：商品和数量必须一致；单价变了就拒绝。库存变化但还够，不影响成交。"""
    if [(line.product_id, line.quantity) for line in current.lines] != [
        (line.product_id, line.quantity) for line in confirmed.lines
    ]:
        raise OrderPreviewChangedError()
    for now, before in zip(current.lines, confirmed.lines, strict=True):
        if now.unit_price_cents != before.unit_price_cents:
            raise OrderPriceChangedError(now.product_name, before.unit_price_cents, now.unit_price_cents)


class Orders:
    """订单：查看自己的订单、预览和下单。每个方法都要传 user_id，只能操作自己的订单。"""

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

    async def preview_order(self, lines: Sequence[OrderLine]) -> OrderPreview:
        """按正式下单的规则报价，返回将要创建的订单。不加锁，也不写入。"""
        _check_lines(lines)
        async with self._session_factory() as session:
            products = await session.scalars(
                select(Product).where(Product.id.in_([line.product_id for line in lines]))
            )
            return _price({product.id: product for product in products}, lines)

    async def place_order(self, user_id: int, lines: Sequence[OrderLine]) -> OrderView:
        """下单（自己开一个事务）。"""
        async with self._session_factory() as session, session.begin():
            return await self.place_order_in(session, user_id, lines)

    async def place_order_in(
        self,
        session: AsyncSession,
        user_id: int,
        lines: Sequence[OrderLine],
        confirmed: OrderPreview | None = None,
    ) -> OrderView:
        """在调用方的事务里下单：只写入，不提交也不回滚，由调用方决定。

        先锁住这些商品（直到调用方的事务结束），再按最新的价格和库存重新报价。
        confirmed 是用户确认过的报价，单价变了就拒绝，不会按新价格悄悄成交。
        """
        _check_lines(lines)
        # 按 id 顺序给这些商品行加锁：别的下单请求要等这个事务结束才能读到库存，
        # 固定顺序则避免两个订单互相等对方的锁（死锁）。
        locked = await session.scalars(
            select(Product)
            .where(Product.id.in_([line.product_id for line in lines]))
            .order_by(Product.id)
            .with_for_update()
        )
        products = {product.id: product for product in locked}
        preview = _price(products, lines)
        if confirmed is not None:
            _ensure_same_prices(preview, confirmed)

        order = Order(user_id=user_id, status="placed", total_cents=preview.total_cents)
        for line in preview.lines:
            products[line.product_id].stock -= line.quantity
            order.items.append(
                OrderItem(
                    product_id=line.product_id,
                    product_name=line.product_name,
                    unit_price_cents=line.unit_price_cents,
                    quantity=line.quantity,
                )
            )
        session.add(order)
        await session.flush()  # 拿到订单号和创建时间
        return OrderView.model_validate(order)
