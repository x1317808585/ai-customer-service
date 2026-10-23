from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import ConversationServiceDep, CurrentUserDep
from app.models import MessageRole

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


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
