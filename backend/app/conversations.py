import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation, Message

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新会话"
TITLE_MAX_LENGTH = 20
HISTORY_MESSAGE_LIMIT = 20

ReplyEventType = Literal["message.accepted", "message.delta", "message.done", "error"]


@dataclass(frozen=True)
class ReplyEvent:
    """回复过程中的一个事件，接口层会把它转换成 SSE 格式发给前端。"""

    type: ReplyEventType
    data: dict[str, Any]


class ConversationNotFoundError(Exception):
    """会话不存在，或者不属于当前用户。两种情况对外表现一致，不泄露别人的会话是否存在。"""


class ConversationBusyError(Exception):
    """这个会话的上一条回复还在生成中。"""


class ConversationService:
    """会话、消息和回复的全部业务逻辑。接口层只负责把 HTTP 请求翻译成对这里的调用。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        chat_graph: CompiledStateGraph,
    ) -> None:
        self._session_factory = session_factory
        self._chat_graph = chat_graph
        self._replying_conversation_ids: set[int] = set()

    async def create_conversation(self, user_id: int) -> Conversation:
        async with self._session_factory() as session:
            conversation = Conversation(user_id=user_id, title=DEFAULT_TITLE)
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def list_conversations(self, user_id: int) -> list[Conversation]:
        async with self._session_factory() as session:
            conversations = await session.scalars(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.id.desc())
            )
            return list(conversations)

    async def list_messages(self, user_id: int, conversation_id: int) -> list[Message]:
        async with self._session_factory() as session:
            await _get_owned_conversation(session, user_id, conversation_id)
            messages = await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
            return list(messages)

    async def send_message(
        self, user_id: int, conversation_id: int, content: str
    ) -> AsyncIterator[ReplyEvent]:
        """保存用户消息，并返回回复的事件流。

        会话不存在或正在回复时，直接抛出异常，此时什么都不会写入；
        一旦正常返回，用户消息已经保存，之后的结果（包括失败）都以事件的形式给出。
        """
        events = self._reply(user_id, conversation_id, content)
        accepted = await anext(events)
        return _prepend(accepted, events)

    async def _reply(
        self, user_id: int, conversation_id: int, content: str
    ) -> AsyncIterator[ReplyEvent]:
        async with self._session_factory() as session:
            await _get_owned_conversation(session, user_id, conversation_id)

        with self._replying_to(conversation_id):
            async with self._session_factory() as session:
                if not await _has_messages(session, conversation_id):
                    conversation = await session.get_one(Conversation, conversation_id)
                    conversation.title = content[:TITLE_MAX_LENGTH]
                user_message = Message(conversation_id=conversation_id, role="user", content=content)
                session.add(user_message)
                await session.commit()
                history = await _load_recent_history(session, conversation_id)
            yield ReplyEvent("message.accepted", {"message_id": user_message.id})

            try:
                reply_text = ""
                async for chunk, _metadata in self._chat_graph.astream(
                    {"messages": history}, stream_mode="messages"
                ):
                    if isinstance(chunk, AIMessageChunk) and chunk.text:
                        reply_text += chunk.text
                        yield ReplyEvent("message.delta", {"text": chunk.text})

                async with self._session_factory() as session:
                    reply = Message(conversation_id=conversation_id, role="assistant", content=reply_text)
                    session.add(reply)
                    await session.commit()
            except Exception:
                logger.exception("会话 %s 的回复生成或保存失败", conversation_id)
                yield ReplyEvent("error", {"detail": "回复生成失败，请稍后重试"})
                return

            yield ReplyEvent("message.done", {"message_id": reply.id})

    @contextmanager
    def _replying_to(self, conversation_id: int) -> Iterator[None]:
        """标记“这个会话正在回复”，结束（包括出错、被取消）时自动清除标记。"""
        if conversation_id in self._replying_conversation_ids:
            raise ConversationBusyError
        self._replying_conversation_ids.add(conversation_id)
        try:
            yield
        finally:
            self._replying_conversation_ids.discard(conversation_id)


async def _get_owned_conversation(
    session: AsyncSession, user_id: int, conversation_id: int
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise ConversationNotFoundError
    return conversation


async def _has_messages(session: AsyncSession, conversation_id: int) -> bool:
    return bool(await session.scalar(select(exists().where(Message.conversation_id == conversation_id))))


async def _load_recent_history(session: AsyncSession, conversation_id: int) -> list[BaseMessage]:
    recent = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_MESSAGE_LIMIT)
    )
    return [
        HumanMessage(message.content) if message.role == "user" else AIMessage(message.content)
        for message in reversed(list(recent))
    ]


async def _prepend(
    first: ReplyEvent, rest: AsyncIterator[ReplyEvent]
) -> AsyncIterator[ReplyEvent]:
    yield first
    async for event in rest:
        yield event
