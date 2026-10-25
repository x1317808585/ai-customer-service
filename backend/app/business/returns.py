from collections.abc import Callable
from dataclasses import dataclass
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
        if remaining <= 0:
            super().__init__("这件商品已经没有可退的数量了")
        else:
            super().__init__(f"最多还能退 {remaining} 件，申请了 {requested} 件")
        self.requested = requested
        self.remaining = remaining


class ReturnPreviewChangedError(BusinessError):
    def __init__(self) -> None:
        super().__init__("退货内容和确认时不一致，请重新发起")


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


class ReturnEligibility(BaseModel):
    """一个订单项现在能不能退。不能退时 reason 写明原因。"""

    model_config = ConfigDict(frozen=True)

    order_item_id: int
    product_name: str
    eligible: bool
    returnable_quantity: int
    return_deadline: datetime | None
    reason: str | None


class ReturnPreview(BaseModel):
    """将要提交的退货申请：用户确认的就是这些内容。退款金额按下单时的单价计算。"""

    model_config = ConfigDict(frozen=True)

    order_id: int
    order_item_id: int
    product_name: str
    quantity: int
    refund_cents: int
    reason: str


@dataclass(frozen=True)
class _ItemFacts:
    """判断一个订单项能不能退所需的数据。"""

    item: OrderItem
    delivered_at: datetime | None
    returnable: bool
    remaining: int


def _checked_reason(quantity: int, reason: str) -> str:
    """检查申请本身的填写，返回去掉首尾空格的原因。"""
    reason = reason.strip()
    if quantity <= 0:
        raise InvalidReturnError("退货数量必须是正数")
    if not reason or len(reason) > REASON_MAX_LENGTH:
        raise InvalidReturnError(f"请填写退货原因（不超过 {REASON_MAX_LENGTH} 个字）")
    return reason


def _preview_of(facts: _ItemFacts, quantity: int, reason: str) -> ReturnPreview:
    return ReturnPreview(
        order_id=facts.item.order_id,
        order_item_id=facts.item.id,
        product_name=facts.item.product_name,
        quantity=quantity,
        refund_cents=facts.item.unit_price_cents * quantity,
        reason=reason,
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

    async def check_eligibility(self, user_id: int, order_item_id: int) -> ReturnEligibility:
        """这个订单项现在能不能退、还能退几件、退货期到什么时候。只查询，不写入。"""
        async with self._session_factory() as session:
            facts = await self._load_facts(session, user_id, order_item_id, lock=False)
        problem = self._rule_problem(facts)
        if problem is None and facts.remaining <= 0:
            problem = ReturnQuantityExceededError(1, facts.remaining)
        return ReturnEligibility(
            order_item_id=facts.item.id,
            product_name=facts.item.product_name,
            eligible=problem is None,
            returnable_quantity=max(facts.remaining, 0) if problem is None else 0,
            return_deadline=self._deadline(facts),
            reason=str(problem) if problem else None,
        )

    async def preview_return(self, user_id: int, order_item_id: int, quantity: int, reason: str) -> ReturnPreview:
        """按正式提交的规则演练一遍，返回将要提交的内容。不加锁，也不写入。"""
        reason = _checked_reason(quantity, reason)
        async with self._session_factory() as session:
            facts = await self._load_facts(session, user_id, order_item_id, lock=False)
        self._ensure_allowed(facts, quantity)
        return _preview_of(facts, quantity, reason)

    async def request_return(self, user_id: int, order_item_id: int, quantity: int, reason: str) -> ReturnView:
        """提交退货申请（自己开一个事务）。"""
        async with self._session_factory() as session, session.begin():
            return await self.request_return_in(session, user_id, order_item_id, quantity, reason)

    async def request_return_in(
        self,
        session: AsyncSession,
        user_id: int,
        order_item_id: int,
        quantity: int,
        reason: str,
        expected: ReturnPreview | None = None,
    ) -> ReturnView:
        """在调用方的事务里提交退货申请：只写入，不提交也不回滚，由调用方决定。

        先锁住订单项（直到调用方的事务结束），再按最新数据重新计算预览。
        expected 是用户确认过的预览，和重新计算的结果不一致时拒绝，不会按新内容悄悄执行。
        """
        reason = _checked_reason(quantity, reason)
        facts = await self._load_facts(session, user_id, order_item_id, lock=True)
        self._ensure_allowed(facts, quantity)
        preview = _preview_of(facts, quantity, reason)
        if expected is not None and preview != expected:
            raise ReturnPreviewChangedError()

        request = ReturnRequest(
            order_item=facts.item,
            quantity=quantity,
            refund_cents=preview.refund_cents,
            reason=reason,
            status="pending",
        )
        session.add(request)
        await session.flush()
        return ReturnView.of(request)

    async def _load_facts(
        self, session: AsyncSession, user_id: int, order_item_id: int, *, lock: bool
    ) -> _ItemFacts:
        """读取判断能不能退所需的全部数据。lock=True 时锁住订单项，同一订单项的其他申请要排队。"""
        query = (
            select(OrderItem, Order.delivered_at, Product.returnable)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .where(OrderItem.id == order_item_id, Order.user_id == user_id)
        )
        if lock:
            query = query.with_for_update(of=OrderItem)
        row = (await session.execute(query)).one_or_none()
        if row is None:
            raise OrderItemNotFoundError(order_item_id)
        item, delivered_at, returnable = row

        used = await session.scalar(
            select(func.coalesce(func.sum(ReturnRequest.quantity), 0)).where(
                ReturnRequest.order_item_id == item.id, ReturnRequest.status.in_(_USES_UP_QUANTITY)
            )
        )
        return _ItemFacts(item=item, delivered_at=delivered_at, returnable=returnable, remaining=item.quantity - used)

    def _deadline(self, facts: _ItemFacts) -> datetime | None:
        return None if facts.delivered_at is None else facts.delivered_at + self._return_window

    def _rule_problem(self, facts: _ItemFacts) -> ReturnNotAllowedError | None:
        """不看数量时，这个订单项按规则能不能退。能退返回 None。"""
        deadline = self._deadline(facts)
        if deadline is None:  # 数据库约束保证：只有已签收的订单才有签收时间
            return OrderNotDeliveredError()
        if self._clock() > deadline:
            return ReturnWindowExpiredError(self._return_window.days)
        if not facts.returnable:
            return ProductNotReturnableError(facts.item.product_name)
        return None

    def _ensure_allowed(self, facts: _ItemFacts, quantity: int) -> None:
        if problem := self._rule_problem(facts):
            raise problem
        if quantity > facts.remaining:
            raise ReturnQuantityExceededError(quantity, facts.remaining)

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
