from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.business.orders import OrderNotFoundError
from app.models import Conversation, Order, Ticket, TicketStatus

SUMMARY_MAX_LENGTH = 1000
LIST_LIMIT = 20


class InvalidTicketError(BusinessError):
    """工单内容有误，比如摘要为空或太长。"""


class SourceConversationNotFoundError(BusinessError):
    def __init__(self, conversation_id: int) -> None:
        super().__init__(f"会话 {conversation_id} 不存在")
        self.conversation_id = conversation_id


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    order_id: int | None
    summary: str
    status: TicketStatus
    created_at: datetime


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


class Tickets:
    """转人工工单：顾客的问题 AI 处理不了时，留下摘要交给人工客服。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

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


async def _ensure_order_owned(session: AsyncSession, user_id: int, order_id: int | None) -> None:
    if order_id is None:
        return
    owned = await session.scalar(select(Order.id).where(Order.id == order_id, Order.user_id == user_id))
    if owned is None:
        raise OrderNotFoundError(order_id)
