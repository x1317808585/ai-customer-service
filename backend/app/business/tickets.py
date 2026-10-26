from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.business.errors import BusinessError
from app.business.orders import OrderNotFoundError
from app.models import Conversation, Order, Ticket, TicketStatus, User

SUMMARY_MAX_LENGTH = 1000
RESOLUTION_MAX_LENGTH = 2000
LIST_LIMIT = 20
STAFF_LIST_LIMIT = 50


class InvalidTicketError(BusinessError):
    """工单内容有误，比如摘要为空或太长。"""


class TicketNotFoundError(BusinessError):
    def __init__(self, ticket_id: int) -> None:
        super().__init__(f"工单 {ticket_id} 不存在")
        self.ticket_id = ticket_id


class TicketAlreadyClosedError(BusinessError):
    def __init__(self) -> None:
        super().__init__("工单已经关闭，不能再处理")


class SourceConversationNotFoundError(BusinessError):
    def __init__(self, conversation_id: int) -> None:
        super().__init__(f"会话 {conversation_id} 不存在")
        self.conversation_id = conversation_id


class TicketView(BaseModel):
    """顾客看到的工单：包含处理结果，不包含处理人。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    order_id: int | None
    summary: str
    status: TicketStatus
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None


class StaffTicketView(TicketView):
    """管理员看到的工单：多了顾客、来源会话和处理人。"""

    customer: str
    conversation_id: int | None
    resolved_by: str | None  # 处理人的用户名


class TicketPreview(BaseModel):
    """将要创建的工单：交给人工客服的问题摘要，以及关联的订单。"""

    model_config = ConfigDict(frozen=True)

    order_id: int | None
    summary: str


def _checked_summary(summary: str) -> str:
    summary = summary.strip()
    if not summary or len(summary) > SUMMARY_MAX_LENGTH:
        raise InvalidTicketError(f"请填写问题摘要（不超过 {SUMMARY_MAX_LENGTH} 个字）")
    return summary


def _checked_resolution(resolution: str) -> str:
    resolution = resolution.strip()
    if not resolution or len(resolution) > RESOLUTION_MAX_LENGTH:
        raise InvalidTicketError(f"请填写处理结果（不超过 {RESOLUTION_MAX_LENGTH} 个字）")
    return resolution


def utc_now() -> datetime:
    return datetime.now(UTC)


class Tickets:
    """转人工工单：顾客的问题 AI 处理不了时，留下摘要交给人工客服；管理员处理后关闭。"""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def list_tickets(self, user_id: int) -> list[TicketView]:
        async with self._session_factory() as session:
            tickets = await session.scalars(
                select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.id.desc()).limit(LIST_LIMIT)
            )
            return [TicketView.model_validate(ticket) for ticket in tickets]

    async def preview_ticket(self, user_id: int, summary: str, order_id: int | None = None) -> TicketPreview:
        """检查工单内容，返回将要创建的工单。只查询，不写入。"""
        summary = _checked_summary(summary)
        async with self._session_factory() as session:
            await _ensure_order_owned(session, user_id, order_id)
        return TicketPreview(order_id=order_id, summary=summary)

    async def open_ticket(self, user_id: int, summary: str, order_id: int | None = None) -> TicketView:
        """创建工单（自己开一个事务）。"""
        async with self._session_factory() as session, session.begin():
            return await self.open_ticket_in(session, user_id, summary, order_id)

    async def open_ticket_in(
        self,
        session: AsyncSession,
        user_id: int,
        summary: str,
        order_id: int | None = None,
        conversation_id: int | None = None,
    ) -> TicketView:
        """在调用方的事务里创建工单：只写入，不提交。

        conversation_id 是工单来自哪个会话（以后人工客服据此查看聊天记录），必须属于这个用户。
        """
        summary = _checked_summary(summary)
        await _ensure_order_owned(session, user_id, order_id)
        if conversation_id is not None:
            owned = await session.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            )
            if owned is None:
                raise SourceConversationNotFoundError(conversation_id)

        ticket = Ticket(
            user_id=user_id, order_id=order_id, conversation_id=conversation_id, summary=summary, status="open"
        )
        session.add(ticket)
        await session.flush()
        return TicketView.model_validate(ticket)

    # ---------- 管理员 ----------
    # admin_id 只用来记录操作人，鉴权由接口层负责（只有管理员能调到这些方法）。

    async def list_for_staff(self, status: TicketStatus) -> list[StaffTicketView]:
        """所有顾客的某种状态的工单，最新的在前。"""
        async with self._session_factory() as session:
            query = _staff_query().where(Ticket.status == status)
            rows = await session.execute(query.order_by(Ticket.id.desc()).limit(STAFF_LIST_LIMIT))
            return [_staff_view(*row) for row in rows]

    async def get_for_staff(self, ticket_id: int) -> StaffTicketView:
        async with self._session_factory() as session:
            row = (await session.execute(_staff_query().where(Ticket.id == ticket_id))).one_or_none()
        if row is None:
            raise TicketNotFoundError(ticket_id)
        return _staff_view(*row)

    async def close_ticket(self, admin_id: int, ticket_id: int, resolution: str) -> StaffTicketView:
        """填写处理结果并关闭工单。锁住工单再检查状态，两个管理员同时关闭时只有一个成功。"""
        resolution = _checked_resolution(resolution)
        async with self._session_factory() as session, session.begin():
            ticket = await session.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
            if ticket is None:
                raise TicketNotFoundError(ticket_id)
            if ticket.status != "open":
                raise TicketAlreadyClosedError()
            ticket.status = "closed"
            ticket.resolution = resolution
            ticket.resolved_by = admin_id
            ticket.resolved_at = self._clock()
        return await self.get_for_staff(ticket_id)


async def _ensure_order_owned(session: AsyncSession, user_id: int, order_id: int | None) -> None:
    if order_id is None:
        return
    owned = await session.scalar(select(Order.id).where(Order.id == order_id, Order.user_id == user_id))
    if owned is None:
        raise OrderNotFoundError(order_id)


_Customer = aliased(User, name="customer")
_Resolver = aliased(User, name="resolver")


def _staff_query() -> Select[tuple[Ticket, str, str | None]]:
    """管理员查询工单：连同顾客用户名、处理人用户名一起查出。"""
    return (
        select(Ticket, _Customer.username, _Resolver.username)
        .join(_Customer, _Customer.id == Ticket.user_id)
        .outerjoin(_Resolver, _Resolver.id == Ticket.resolved_by)
    )


def _staff_view(ticket: Ticket, customer: str, resolved_by: str | None) -> StaffTicketView:
    return StaffTicketView(
        **TicketView.model_validate(ticket).model_dump(),
        customer=customer,
        conversation_id=ticket.conversation_id,
        resolved_by=resolved_by,
    )
