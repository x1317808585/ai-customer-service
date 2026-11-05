import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import aclosing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.actions import ActionService, ActionView
from app.agent.events import ActionProposed, DraftDiscarded, KnowledgeFound, TextDelta, ToolStatus
from app.agent.reply_agent import ReplyAgent, ReplyFailedError
from app.citations import Citation, cited
from app.memory import ConversationMemory, ConversationNotFoundError
from app.models import Conversation, Message, MessageRole

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新会话"
TITLE_MAX_LENGTH = 20

ReplyEventType = Literal[
    "message.accepted",
    "message.delta",
    "message.discard",
    "tool.status",
    "action.proposed",
    "message.done",
    "error",
]


@dataclass(frozen=True)
class ReplyEvent:
    """回复过程中的一个事件，接口层会把它转换成 SSE 格式发给前端。"""

    type: ReplyEventType
    data: dict[str, Any]


@dataclass(frozen=True)
class HistoryMessage:
    """历史记录里的一条消息。确认卡片消息带着对应操作的当前状态，助手回复带着它引用的原文快照。"""

    id: int
    role: MessageRole
    content: str
    created_at: datetime
    action: ActionView | None
    citations: list[Citation]


class ConversationBusyError(Exception):
    """这个会话的上一条回复还在生成中。"""


class ConversationService:
    """会话、消息和回复的全部业务逻辑。接口层只负责把 HTTP 请求翻译成对这里的调用。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        reply_agent: ReplyAgent,
        actions: ActionService,
        memory: ConversationMemory | None = None,
    ) -> None:
        """memory 只在测试里传（比如换一个会失败的摘要器）；不传时用默认的上限和离线摘要器。"""
        self._session_factory = session_factory
        self._reply_agent = reply_agent
        self._actions = actions
        self._memory = memory or ConversationMemory(session_factory, actions)
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

    async def list_messages(self, user_id: int, conversation_id: int) -> list[HistoryMessage]:
        async with self._session_factory() as session:
            await _get_owned_conversation(session, user_id, conversation_id)
            return await self._history_of(session, conversation_id)

    async def transcript_for_staff(self, conversation_id: int) -> list[HistoryMessage]:
        """工作人员查看一个会话的全部消息。不检查会话归属，所以只给已鉴权的工单详情流程使用，
        conversation_id 必须来自工单里保存的来源会话。会话已被删除时返回空列表。"""
        async with self._session_factory() as session:
            return await self._history_of(session, conversation_id)

    async def _history_of(self, session: AsyncSession, conversation_id: int) -> list[HistoryMessage]:
        messages = await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .options(selectinload(Message.action))
            .order_by(Message.id)
        )
        return [
            HistoryMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                created_at=message.created_at,
                action=self._actions.view(message.action) if message.action else None,
                citations=[Citation.model_validate(citation) for citation in message.citations or []],
            )
            for message in messages
        ]

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
    ) -> AsyncGenerator[ReplyEvent]:
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
            yield ReplyEvent("message.accepted", {"message_id": user_message.id})

            try:
                # 用户消息已经保存并告诉了前端，之后的任何失败都以 error 事件结束这一轮
                history = await self._memory.context_for(user_id, conversation_id)
                # 只保存最终回答：被撤回的草稿（模型调用工具前说的话）不算。
                # aclosing：这里提前结束（比如用户点了停止）时，主动关闭 Agent，让它马上断开 MCP 连接。
                reply_text = ""
                candidates: list[Citation] = []  # 本轮检索给出的知识库原文，最终回复只能引用它们
                async with aclosing(self._reply_agent.stream_reply(user_id, history)) as agent_events:
                    async for event in agent_events:
                        match event:
                            case TextDelta(text=text):
                                reply_text += text
                                yield ReplyEvent("message.delta", {"text": text})
                            case DraftDiscarded():
                                reply_text = ""
                                yield ReplyEvent("message.discard", {})
                            case KnowledgeFound(passages=passages):
                                candidates.extend(Citation.model_validate(passage) for passage in passages)
                            case ToolStatus(call_id=call_id, tool=tool, status=tool_status):
                                yield ReplyEvent(
                                    "tool.status", {"call_id": call_id, "tool": tool, "status": tool_status}
                                )
                            case ActionProposed(kind=kind, arguments=arguments, preview=preview):
                                # 先在一个事务里保存卡片消息和待确认操作，提交成功后才推送给前端。
                                # 这样之后即使回复失败或被停止，刷新页面时卡片也还在。
                                async with self._session_factory() as session, session.begin():
                                    action = await self._actions.propose_in(
                                        session,
                                        user_id=user_id,
                                        conversation_id=conversation_id,
                                        source_message_id=user_message.id,
                                        kind=kind,
                                        arguments=arguments,
                                        preview=preview,
                                    )
                                yield ReplyEvent(
                                    "action.proposed",
                                    {
                                        "message_id": action.card_message_id,
                                        "action": self._actions.view(action).model_dump(mode="json"),
                                    },
                                )

                # 引用和正文作为同一条消息一起保存；拒答、中断时不会走到这里，候选原文不会挂到页面上
                citations = cited(reply_text, candidates)
                async with self._session_factory() as session:
                    reply = Message(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=reply_text,
                        citations=[citation.model_dump() for citation in citations] or None,
                    )
                    session.add(reply)
                    await session.commit()
            except ReplyFailedError as error:
                logger.warning("会话 %s 的回复没有完成：%s", conversation_id, error)
                yield ReplyEvent("error", {"detail": str(error)})
                return
            except Exception:
                logger.exception("会话 %s 的回复生成或保存失败", conversation_id)
                yield ReplyEvent("error", {"detail": "回复生成失败，请稍后重试"})
                return

            yield ReplyEvent(
                "message.done",
                {"message_id": reply.id, "citations": [citation.model_dump() for citation in citations]},
            )

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


async def _prepend(
    first: ReplyEvent, rest: AsyncGenerator[ReplyEvent]
) -> AsyncIterator[ReplyEvent]:
    yield first
    async with aclosing(rest):  # 这里被提前关闭时，把后面的生成器也一起关掉
        async for event in rest:
            yield event
