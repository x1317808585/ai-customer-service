from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload
from app.business.errors import BusinessError
from app.models import Order, OrderItem, Product, ReturnRequest, ReturnStatus

RETURN_WINDOW = timedelta(days=7)
REASON_MAX_LENGTH = 500
LIST_LIMIT = 20

# 这些状态的退货会占用可退数量；已拒绝、已取消的不占用。
_USES_UP_QUANTITY: tuple[ReturnStatus, ...] = ("pending", "completed")


class OrderItemNotFoundError(BusinessError):
    def __init__(self, order_item_id: int) -> None:
        super().__init__(f"订单项 {order_item_id} 不存在")
        self.order_item_id = order_item_id


class ReturnNotFoundError(BusinessError):
    def __init__(self, return_id: int) -> None:
        super().__init__(f"退货申请 {return_id} 不存在")
        self.return_id = return_id


class InvalidReturnError(BusinessError):
    """申请本身填写有误，比如数量不是正数、没写原因。"""


class ReturnNotAllowedError(BusinessError):
    """申请填写没问题，但按退货规则不能退。下面几种是具体原因。"""


class OrderNotDeliveredError(ReturnNotAllowedError):
    def __init__(self) -> None:
        super().__init__("订单还没有签收，签收后才能申请退货")


class ReturnWindowExpiredError(ReturnNotAllowedError):
    def __init__(self, days: int) -> None:
        super().__init__(f"已超过签收后 {days} 天的退货期")


class ProductNotReturnableError(ReturnNotAllowedError):
    def __init__(self, product_name: str) -> None:
        super().__init__(f"「{product_name}」不支持退货")


class ReturnQuantityExceededError(ReturnNotAllowedError):
    def __init__(self, requested: int, remaining: int) -> None:
        super().__init__(f"最多还能退 {remaining} 件，申请了 {requested} 件")
        self.requested = requested
        self.remaining = remaining


class ReturnNotPendingError(BusinessError):
    def __init__(self, status: ReturnStatus) -> None:
        super().__init__(f"退货申请已经是 {status} 状态，不能再处理")
        self.status = status


class ReturnView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    order_id: int
    order_item_id: int
    product_name: str
    quantity: int
    refund_cents: int
    reason: str
    status: ReturnStatus
    created_at: datetime
    resolved_at: datetime | None

    @classmethod
    def of(cls, request: ReturnRequest) -> "ReturnView":
        return cls(
            id=request.id,
            order_id=request.order_item.order_id,
            order_item_id=request.order_item_id,
            product_name=request.order_item.product_name,
            quantity=request.quantity,
            refund_cents=request.refund_cents,
            reason=request.reason,
            status=request.status,
            created_at=request.created_at,
            resolved_at=request.resolved_at,
        )


def utc_now() -> datetime:
    return datetime.now(UTC)


class Returns:
    """退货：顾客申请、查看、取消自己的退货；客服（管理员）完成或拒绝退货。

    return_window 是签收后可以申请退货的时长；clock 返回"现在"，测试时可以换成固定时间。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        return_window: timedelta = RETURN_WINDOW,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._return_window = return_window
        self._clock = clock

    async def list_returns(self, user_id: int) -> list[ReturnView]:
        async with self._session_factory() as session:
            requests = await session.scalars(
                select(ReturnRequest)
                .join(ReturnRequest.order_item)
                .join(Order, Order.id == OrderItem.order_id)
                .where(Order.user_id == user_id)
                .options(joinedload(ReturnRequest.order_item))
                .order_by(ReturnRequest.id.desc())
                .limit(LIST_LIMIT)
            )
            return [ReturnView.of(request) for request in requests]

    async def request_return(self, user_id: int, order_item_id: int, quantity: int, reason: str) -> ReturnView:
        reason = reason.strip()
        if quantity <= 0:
            raise InvalidReturnError("退货数量必须是正数")
        if not reason or len(reason) > REASON_MAX_LENGTH:
            raise InvalidReturnError(f"请填写退货原因（不超过 {REASON_MAX_LENGTH} 个字）")

        async with self._session_factory() as session, session.begin():
            # 锁住这个订单项：同一订单项的其他退货申请要等这里结束，才能计算可退数量。
            row = (
                await session.execute(
                    select(OrderItem, Order.delivered_at, Product.returnable)
                    .join(Order, Order.id == OrderItem.order_id)
                    .join(Product, Product.id == OrderItem.product_id)
                    .where(OrderItem.id == order_item_id, Order.user_id == user_id)
                    .with_for_update(of=OrderItem)
                )
            ).one_or_none()
            if row is None:
                raise OrderItemNotFoundError(order_item_id)
            item, delivered_at, returnable = row

            if delivered_at is None:  # 数据库约束保证：只有已签收的订单才有签收时间
                raise OrderNotDeliveredError()
            if self._clock() > delivered_at + self._return_window:
                raise ReturnWindowExpiredError(self._return_window.days)
            if not returnable:
                raise ProductNotReturnableError(item.product_name)

            used = await session.scalar(
                select(func.coalesce(func.sum(ReturnRequest.quantity), 0)).where(
                    ReturnRequest.order_item_id == item.id, ReturnRequest.status.in_(_USES_UP_QUANTITY)
                )
            )
            remaining = item.quantity - used
            if quantity > remaining:
                raise ReturnQuantityExceededError(quantity, remaining)

            request = ReturnRequest(
                order_item=item,
                quantity=quantity,
                refund_cents=item.unit_price_cents * quantity,
                reason=reason,
                status="pending",
            )
            session.add(request)

        return ReturnView.of(request)

    async def cancel_return(self, user_id: int, return_id: int) -> ReturnView:
        """顾客撤回自己还在处理中的退货申请。"""
        return await self._finish(return_id, "cancelled", owner_id=user_id)

    async def complete_return(self, return_id: int) -> ReturnView:
        """客服确认收到退货：申请变为已完成，商品放回库存。"""
        return await self._finish(return_id, "completed")

    async def reject_return(self, return_id: int) -> ReturnView:
        """客服拒绝退货申请，库存不变。"""
        return await self._finish(return_id, "rejected")

    async def _finish(self, return_id: int, status: ReturnStatus, owner_id: int | None = None) -> ReturnView:
        async with self._session_factory() as session, session.begin():
            # 锁住这条申请：两个人同时点"完成"时，后一个会看到它已经不是 pending，不会重复入库。
            query = (
                select(ReturnRequest)
                .join(ReturnRequest.order_item)
                .join(Order, Order.id == OrderItem.order_id)
                .where(ReturnRequest.id == return_id)
                .options(joinedload(ReturnRequest.order_item))
                .with_for_update(of=ReturnRequest)
            )
            if owner_id is not None:
                query = query.where(Order.user_id == owner_id)
            request = await session.scalar(query)
            if request is None:
                raise ReturnNotFoundError(return_id)
            if request.status != "pending":
                raise ReturnNotPendingError(request.status)

            request.status = status
            request.resolved_at = self._clock()
            if status == "completed":
                await session.execute(
                    update(Product)
                    .where(Product.id == request.order_item.product_id)
                    .values(stock=Product.stock + request.quantity)
                )

        return ReturnView.of(request)
