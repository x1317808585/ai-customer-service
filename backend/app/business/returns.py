from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased, joinedload

from app.business.errors import BusinessError
from app.models import Order, OrderItem, Product, ReturnRequest, ReturnStatus, User

RETURN_WINDOW = timedelta(days=7)
REASON_MAX_LENGTH = 500
NOTE_MAX_LENGTH = 500
LIST_LIMIT = 20
STAFF_LIST_LIMIT = 50

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


# 退货申请处理完之后的状态，用在给顾客看的提示里
_FINISHED_TEXT: dict[ReturnStatus, str] = {
    "pending": "还在处理中",
    "completed": "已经完成",
    "rejected": "已经被拒绝",
    "cancelled": "已经撤回",
}


class ReturnNotPendingError(BusinessError):
    def __init__(self, status: ReturnStatus) -> None:
        super().__init__(f"退货申请{_FINISHED_TEXT[status]}，不能再处理")
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
    resolution_note: str | None  # 完成时的备注、拒绝的原因。不包含处理人
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
            resolution_note=request.resolution_note,
            created_at=request.created_at,
            resolved_at=request.resolved_at,
        )


class StaffReturnView(ReturnView):
    """管理员看到的退货申请：多了顾客、签收时间和处理人。"""

    customer: str
    delivered_at: datetime | None
    resolved_by: str | None  # 处理人的用户名；历史记录可能为空


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


class ReturnCancellationPreview(BaseModel):
    """将要撤回的退货申请。退货单创建后内容不会再变，所以执行时只需重新检查状态。"""

    model_config = ConfigDict(frozen=True)

    return_id: int
    order_id: int
    order_item_id: int
    product_name: str
    quantity: int
    refund_cents: int


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


def _checked_note(note: str | None, *, required: bool) -> str | None:
    """检查管理员填写的备注或原因，返回去掉首尾空格的内容（没填时返回 None）。"""
    note = (note or "").strip() or None
    if required and note is None:
        raise InvalidReturnError(f"请填写拒绝原因（不超过 {NOTE_MAX_LENGTH} 个字）")
    if note is not None and len(note) > NOTE_MAX_LENGTH:
        raise InvalidReturnError(f"备注不能超过 {NOTE_MAX_LENGTH} 个字")
    return note


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

    async def preview_cancellation(self, user_id: int, return_id: int) -> ReturnCancellationPreview:
        """检查这条退货申请现在能不能撤回，返回将要撤回的内容。不加锁，也不写入。"""
        async with self._session_factory() as session:
            request = await session.scalar(_return_query(return_id, owner_id=user_id))
        if request is None:
            raise ReturnNotFoundError(return_id)
        if request.status != "pending":
            raise ReturnNotPendingError(request.status)
        return ReturnCancellationPreview(
            return_id=request.id,
            order_id=request.order_item.order_id,
            order_item_id=request.order_item_id,
            product_name=request.order_item.product_name,
            quantity=request.quantity,
            refund_cents=request.refund_cents,
        )

    async def cancel_return(self, user_id: int, return_id: int) -> ReturnView:
        """顾客撤回自己还在处理中的退货申请（自己开一个事务）。"""
        async with self._session_factory() as session, session.begin():
            return await self.cancel_return_in(session, user_id, return_id)

    async def cancel_return_in(self, session: AsyncSession, user_id: int, return_id: int) -> ReturnView:
        """在调用方的事务里撤回退货申请：只写入，不提交。库存不变（只有完成退货才放回库存）。"""
        return await self._finish_in(session, return_id, "cancelled", actor_id=user_id, owner_id=user_id)

    # ---------- 管理员 ----------
    # admin_id 只用来记录操作人，鉴权由接口层负责（只有管理员能调到这些方法）。

    async def list_for_staff(self, status: ReturnStatus) -> list[StaffReturnView]:
        """所有顾客的某种状态的退货申请，最新的在前。"""
        async with self._session_factory() as session:
            query = _staff_query().where(ReturnRequest.status == status)
            rows = await session.execute(query.order_by(ReturnRequest.id.desc()).limit(STAFF_LIST_LIMIT))
            return [_staff_view(*row) for row in rows]

    async def get_for_staff(self, return_id: int) -> StaffReturnView:
        async with self._session_factory() as session:
            row = (await session.execute(_staff_query().where(ReturnRequest.id == return_id))).one_or_none()
        if row is None:
            raise ReturnNotFoundError(return_id)
        return _staff_view(*row)

    async def complete_return(self, admin_id: int, return_id: int, note: str | None = None) -> StaffReturnView:
        """管理员确认收到退货：申请变为已完成，商品放回库存。备注可以不填。

        这里只改系统状态和库存，项目没有对接支付，不代表退款已经到账。
        """
        note = _checked_note(note, required=False)
        async with self._session_factory() as session, session.begin():
            await self._finish_in(session, return_id, "completed", actor_id=admin_id, note=note)
        return await self.get_for_staff(return_id)

    async def reject_return(self, admin_id: int, return_id: int, reason: str) -> StaffReturnView:
        """管理员拒绝退货申请，原因必须填写（顾客能看到），库存不变。"""
        note = _checked_note(reason, required=True)
        async with self._session_factory() as session, session.begin():
            await self._finish_in(session, return_id, "rejected", actor_id=admin_id, note=note)
        return await self.get_for_staff(return_id)

    async def _finish_in(
        self,
        session: AsyncSession,
        return_id: int,
        status: ReturnStatus,
        *,
        actor_id: int,
        owner_id: int | None = None,
        note: str | None = None,
    ) -> ReturnView:
        # 锁住这条申请：两个人同时处理（比如顾客撤回、管理员完成）时，后一个会看到它已经不是 pending。
        request = await session.scalar(_return_query(return_id, owner_id).with_for_update(of=ReturnRequest))
        if request is None:
            raise ReturnNotFoundError(return_id)
        if request.status != "pending":
            raise ReturnNotPendingError(request.status)

        request.status = status
        request.resolved_by = actor_id
        request.resolution_note = note
        request.resolved_at = self._clock()
        if status == "completed":
            await session.execute(
                update(Product)
                .where(Product.id == request.order_item.product_id)
                .values(stock=Product.stock + request.quantity)
            )
        await session.flush()
        return ReturnView.of(request)


def _return_query(return_id: int, owner_id: int | None) -> Select[tuple[ReturnRequest]]:
    """查一条退货申请（连同订单项）。owner_id 不为空时，只查这个顾客自己的。"""
    query = (
        select(ReturnRequest)
        .join(ReturnRequest.order_item)
        .join(Order, Order.id == OrderItem.order_id)
        .where(ReturnRequest.id == return_id)
        .options(joinedload(ReturnRequest.order_item))
    )
    if owner_id is not None:
        query = query.where(Order.user_id == owner_id)
    return query


_Customer = aliased(User, name="customer")
_Resolver = aliased(User, name="resolver")


def _staff_query() -> Select[tuple[ReturnRequest, str, datetime | None, str | None]]:
    """管理员查询退货申请：连同顾客用户名、签收时间、处理人用户名一起查出。"""
    return (
        select(ReturnRequest, _Customer.username, Order.delivered_at, _Resolver.username)
        .join(ReturnRequest.order_item)
        .join(Order, Order.id == OrderItem.order_id)
        .join(_Customer, _Customer.id == Order.user_id)
        .outerjoin(_Resolver, _Resolver.id == ReturnRequest.resolved_by)
        .options(joinedload(ReturnRequest.order_item))
    )


def _staff_view(
    request: ReturnRequest, customer: str, delivered_at: datetime | None, resolved_by: str | None
) -> StaffReturnView:
    return StaffReturnView(
        **ReturnView.of(request).model_dump(), customer=customer, delivered_at=delivered_at, resolved_by=resolved_by
    )
