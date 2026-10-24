from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.business.errors import BusinessError
from app.business.orders import OrderNotFoundError
from app.models import Order, Ticket, TicketStatus

SUMMARY_MAX_LENGTH = 1000
LIST_LIMIT = 20


class InvalidTicketError(BusinessError):
    """工单内容有误，比如摘要为空或太长。"""


class TicketView(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: int
    order_id: int | None
    summary: str
    status: TicketStatus
    created_at: datetime


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

    async def open_ticket(self, user_id: int, summary: str, order_id: int | None = None) -> TicketView:
        summary = summary.strip()
        if not summary or len(summary) > SUMMARY_MAX_LENGTH:
            raise InvalidTicketError(f"请填写问题摘要（不超过 {SUMMARY_MAX_LENGTH} 个字）")

        async with self._session_factory() as session, session.begin():
            if order_id is not None:
                owned = await session.scalar(select(Order.id).where(Order.id == order_id, Order.user_id == user_id))
                if owned is None:
                    raise OrderNotFoundError(order_id)
            ticket = Ticket(user_id=user_id, order_id=order_id, summary=summary, status="open")
            session.add(ticket)

        return TicketView.model_validate(ticket)
