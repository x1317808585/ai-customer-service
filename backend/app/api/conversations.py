import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import datetime
from typing import Annotated, Any

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

# 生成回复时超过这么多秒没有任何事件，就发一个心跳，告诉前端连接还活着。
# 这是本项目的取值，不是行业标准；前端 45 秒（大约连续错过 3 次心跳）收不到任何事件，按连接中断处理
HEARTBEAT_SECONDS = 15.0

_END = object()  # 回复事件流正常结束的标记


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
    """把回复事件转成 SSE 文本；超过 HEARTBEAT_SECONDS 没有事件时，补发一个心跳。

    回复由一个后台任务从头到尾生成：Agent 里的整次回复时限和 MCP 连接都绑定在创建它们的任务上，
    不能每取一个事件换一个任务。事件放进队列，这里从队列里取；超时只补发心跳，不打断生成，
    也不取消原有的整次回复时限（到时照样以 error 事件结束）。
    顾客断开连接时这里会被取消，随即取消后台任务。取消发生在回复生成的内部，和没有心跳时一样，
    未完成的回复不会保存。
    """
    queue: asyncio.Queue[object] = asyncio.Queue()  # 不设上限：放入从不等待

    async def produce() -> None:
        try:
            async for event in events:
                queue.put_nowait(event)
        except Exception as error:  # 交给下面抛出，和没有心跳时一样由响应处理
            queue.put_nowait(error)
        else:
            queue.put_nowait(_END)

    producer = asyncio.create_task(produce())
    try:
        while True:
            try:
                async with asyncio.timeout(HEARTBEAT_SECONDS):
                    item = await queue.get()
            except TimeoutError:
                yield _sse("heartbeat", {})
                continue
            if item is _END:
                return
            if isinstance(item, Exception):
                raise item
            assert isinstance(item, ReplyEvent)
            yield _sse(item.type, item.data)
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer


def _sse(event_type: str, data: Any) -> str:
    """一个完整的 SSE 事件：事件名、数据，最后一个空行。心跳也带 data：前端的解析器会忽略没有 data 的事件。"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
