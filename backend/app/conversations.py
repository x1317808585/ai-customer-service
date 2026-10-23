from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Conversation, Message

DEFAULT_TITLE = "新会话"


class ConversationNotFoundError(Exception):
    """会话不存在，或者不属于当前用户。两种情况对外表现一致，不泄露别人的会话是否存在。"""


class ConversationService:
    """会话、消息和回复的全部业务逻辑。接口层只负责把 HTTP 请求翻译成对这里的调用。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        chat_graph: CompiledStateGraph,
    ) -> None:
        self._session_factory = session_factory
        self._chat_graph = chat_graph

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


async def _get_owned_conversation(
    session: AsyncSession, user_id: int, conversation_id: int
) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise ConversationNotFoundError
    return conversation
