import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from app.actions import ActionView
from app.api.dependencies import ConversationServiceDep, CurrentUserDep
from app.citations import Citation
from app.conversations import ReplyEvent
from app.models import MessageRole

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

MAX_MESSAGE_LENGTH = 4000


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: MessageRole
    content: str
    created_at: datetime
    action: ActionView | None  # 这条消息是确认卡片时，对应操作的当前状态
    citations: list[Citation]  # 助手回复引用的知识库原文快照，没有引用时为空列表


class SendMessageIn(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_LENGTH)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    user: CurrentUserDep, service: ConversationServiceDep
) -> ConversationOut:
    conversation = await service.create_conversation(user.id)
    return ConversationOut.model_validate(conversation)


@router.get("")
async def list_conversations(
    user: CurrentUserDep, service: ConversationServiceDep
) -> list[ConversationOut]:
    conversations = await service.list_conversations(user.id)
    return [ConversationOut.model_validate(conversation) for conversation in conversations]


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: int, user: CurrentUserDep, service: ConversationServiceDep
) -> list[MessageOut]:
    messages = await service.list_messages(user.id, conversation_id)
    return [MessageOut.model_validate(message) for message in messages]


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageIn,
    user: CurrentUserDep,
    service: ConversationServiceDep,
) -> StreamingResponse:
    events = await service.send_message(user.id, conversation_id, body.content)
    return StreamingResponse(
        _to_sse(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _to_sse(events: AsyncIterator[ReplyEvent]) -> AsyncIterator[str]:
    async for event in events:
        data = json.dumps(event.data, ensure_ascii=False)
        yield f"event: {event.type}\ndata: {data}\n\n"
