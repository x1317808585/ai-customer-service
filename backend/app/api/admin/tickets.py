from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.admin.errors import StaleRecordError
from app.api.conversations import MessageOut
from app.api.dependencies import AdminDep, ConversationServiceDep, TicketsDep
from app.business.tickets import InvalidTicketError, StaffTicketView, TicketAlreadyClosedError, TicketNotFoundError
from app.models import TicketStatus

router = APIRouter(prefix="/tickets")


class TicketDetailOut(BaseModel):
    ticket: StaffTicketView
    transcript: list[MessageOut]  # 来源会话的全部消息（只读）；没有来源会话时为空


class CloseTicketIn(BaseModel):
    """只接受处理结果。处理人取自当前登录的管理员，请求里带了别的字段直接返回 422。"""

    model_config = ConfigDict(extra="forbid")

    resolution: str


@router.get("")
async def list_tickets(tickets: TicketsDep, status: TicketStatus = "open") -> list[StaffTicketView]:
    return await tickets.list_for_staff(status)


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: int, tickets: TicketsDep, conversations: ConversationServiceDep) -> TicketDetailOut:
    ticket = await _get(tickets, ticket_id)
    # 只查看工单里保存的来源会话，不接受请求里指定的会话
    transcript = (
        await conversations.transcript_for_staff(ticket.conversation_id) if ticket.conversation_id else []
    )
    return TicketDetailOut(ticket=ticket, transcript=[MessageOut.model_validate(m) for m in transcript])


@router.post("/{ticket_id}/close")
async def close_ticket(ticket_id: int, body: CloseTicketIn, admin: AdminDep, tickets: TicketsDep) -> StaffTicketView:
    try:
        return await tickets.close_ticket(admin.id, ticket_id, body.resolution)
    except TicketNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except InvalidTicketError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except TicketAlreadyClosedError as error:
        raise StaleRecordError(str(error), "ticket", await tickets.get_for_staff(ticket_id)) from error


async def _get(tickets: TicketsDep, ticket_id: int) -> StaffTicketView:
    try:
        return await tickets.get_for_staff(ticket_id)
    except TicketNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
