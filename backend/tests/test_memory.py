"""会话记忆：按轮裁剪、先摘要再截取、摘要里的卡片显示最新状态、摘要保存失败照常回复、只读自己的会话。"""

from collections.abc import Awaitable, Callable, Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.action_kinds import build_action_kinds
from app.actions import ActionService
from app.agent.reply_agent import ReplyAgent
from app.api.conversations import MAX_MESSAGE_LENGTH
from app.business.orders import OrderLine, Orders
from app.business.returns import Returns
from app.business.tickets import Tickets
from app.conversations import ConversationService
from app.memory import (
    MESSAGE_OVERHEAD_TOKENS,
    ContextLimits,
    ConversationMemory,
    ConversationNotFoundError,
    estimate_tokens,
)
from app.models import Conversation, ConversationSummary, Message, MessageRole, User
from tests.agent.doubles import FakeToolbox, ScriptedChatModel, always

AddProduct = Callable[..., Awaitable[int]]
SessionFactory = async_sessionmaker[AsyncSession]

# 测试用的小上限：最多保留 3 轮原文，摘要之后的原文超过 500 就重新摘要
SMALL = ContextLimits(
    budget_tokens=600,
    summarize_at_tokens=500,
    keep_rounds=3,
    max_question_tokens=50,
    summary_max_tokens=60,
    summary_max_actions=3,
)


@pytest.fixture
def actions(session_factory: SessionFactory) -> ActionService:
    kinds = build_action_kinds(Orders(session_factory), Returns(session_factory), Tickets(session_factory))
    return ActionService(session_factory, kinds)


@pytest.fixture
def memory(session_factory: SessionFactory, actions: ActionService) -> ConversationMemory:
    return ConversationMemory(session_factory, actions, limits=SMALL)


@pytest.fixture
async def conversation_id(session_factory: SessionFactory, users: dict[str, User]) -> int:
    async with session_factory() as session:
        conversation = Conversation(user_id=users["alice"].id, title="测试")
        session.add(conversation)
        await session.commit()
        return conversation.id


async def add_message(session_factory: SessionFactory, conversation_id: int, role: MessageRole, content: str) -> int:
    async with session_factory() as session:
        message = Message(conversation_id=conversation_id, role=role, content=content)
        session.add(message)
        await session.commit()
        return message.id


async def add_round(session_factory: SessionFactory, conversation_id: int, question: str, reply: str = "好的") -> None:
    await add_message(session_factory, conversation_id, "user", question)
    await add_message(session_factory, conversation_id, "assistant", reply)


async def add_order_card_round(
    session_factory: SessionFactory, actions: ActionService, conversation_id: int, user_id: int, product_id: int
) -> int:
    """一轮“下单”：用户消息 → 待确认的下单卡片 → 助手说明。返回操作编号。"""
    question_id = await add_message(session_factory, conversation_id, "user", f"下单商品 {product_id}")
    lines = [OrderLine(product_id=product_id, quantity=1)]
    preview = await Orders(session_factory).preview_order(lines)
    async with session_factory.begin() as session:
        action = await actions.propose_in(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            source_message_id=question_id,
            kind="order",
            arguments={"lines": [line.model_dump() for line in lines]},
            preview=preview.model_dump(),
        )
    await add_message(session_factory, conversation_id, "assistant", "请在卡片里确认")
    return action.id


async def summaries_of(session_factory: SessionFactory, conversation_id: int) -> list[ConversationSummary]:
    async with session_factory() as session:
        return list(
            await session.scalars(
                select(ConversationSummary)
                .where(ConversationSummary.conversation_id == conversation_id)
                .order_by(ConversationSummary.id)
            )
        )


def total_tokens(context: Sequence[BaseMessage]) -> int:
    return sum(estimate_tokens(message.text) + MESSAGE_OVERHEAD_TOKENS for message in context)


def questions_in(context: Sequence[BaseMessage]) -> list[str]:
    return [message.text for message in context if isinstance(message, HumanMessage)]


# ---------- 裁剪和摘要 ----------


async def test_short_conversation_is_given_in_full_without_a_summary(
    memory: ConversationMemory, session_factory: SessionFactory, conversation_id: int, users: dict[str, User]
) -> None:
    await add_round(session_factory, conversation_id, "第一句", "回复一")
    await add_message(session_factory, conversation_id, "user", "第二句")

    context = await memory.context_for(users["alice"].id, conversation_id)

    assert [(type(message), message.text) for message in context] == [
        (HumanMessage, "第一句"),
        (AIMessage, "回复一"),
        (HumanMessage, "第二句"),
    ]
    assert await summaries_of(session_factory, conversation_id) == []


async def test_old_rounds_are_summarized_before_they_would_be_dropped(
    memory: ConversationMemory, session_factory: SessionFactory, conversation_id: int, users: dict[str, User]
) -> None:
    await add_round(session_factory, conversation_id, "订单 5 到哪了", "订单 5 已发货")
    for index in range(2, 5):
        await add_round(session_factory, conversation_id, f"第 {index} 个问题")
    await add_message(session_factory, conversation_id, "user", "当前问题")

    context = await memory.context_for(users["alice"].id, conversation_id)

    summary = context[0]
    assert isinstance(summary, SystemMessage)
    assert "提到的订单号：5" in summary.text
    assert questions_in(context) == ["第 3 个问题", "第 4 个问题", "当前问题"]  # 原文最多 3 轮，含当前这一轮
    [saved] = await summaries_of(session_factory, conversation_id)
    assert "订单 5 到哪了" in saved.content  # 被摘要覆盖的原文不再重复提供，但问过什么记在摘要里


async def test_summary_shows_the_latest_state_of_old_cards(
    memory: ConversationMemory,
    actions: ActionService,
    session_factory: SessionFactory,
    conversation_id: int,
    users: dict[str, User],
    add_product: AddProduct,
) -> None:
    """生成摘要 → 确认那张旧卡片 → 再提问：上下文里是已执行的状态和订单号。"""
    alice = users["alice"].id
    product_id = await add_product("SoundPods 降噪耳机", price_cents=59900)
    action_id = await add_order_card_round(session_factory, actions, conversation_id, alice, product_id)
    for index in range(2, 5):
        await add_round(session_factory, conversation_id, f"第 {index} 个问题")
    await add_message(session_factory, conversation_id, "user", "当前问题")
    before = await memory.context_for(alice, conversation_id)

    confirmed = await actions.confirm(alice, action_id)
    await add_message(session_factory, conversation_id, "assistant", "好的")
    await add_message(session_factory, conversation_id, "user", "刚才下单成功了吗")
    after = await memory.context_for(alice, conversation_id)

    assert f"（下单 #{action_id}。状态：等待顾客确认）" in before[0].text
    assert confirmed.result is not None
    assert f"（下单 #{action_id}。状态：模拟订单已创建，订单号 {confirmed.result['order_id']}" in after[0].text


async def test_long_question_long_summary_and_many_old_cards_stay_within_budget(
    memory: ConversationMemory,
    actions: ActionService,
    session_factory: SessionFactory,
    conversation_id: int,
    users: dict[str, User],
    add_product: AddProduct,
) -> None:
    alice = users["alice"].id
    product_id = await add_product("SoundPods 降噪耳机", price_cents=59900)
    for _ in range(6):
        await add_order_card_round(session_factory, actions, conversation_id, alice, product_id)
        await add_round(session_factory, conversation_id, "很长的问题" * 20, "很长的回复" * 20)
    longest_question = "问" * SMALL.max_question_tokens
    await add_message(session_factory, conversation_id, "user", longest_question)

    context = await memory.context_for(alice, conversation_id)

    assert total_tokens(context) <= SMALL.budget_tokens
    assert context[-1] == HumanMessage(longest_question)
    assert context[0].text.count("（下单 #") == SMALL.summary_max_actions


# ---------- 失败和归属 ----------


class UnsavableSummarizer:
    """返回数据库存不下的内容（文本里有 NUL 字符），用来制造一次摘要保存失败。"""

    async def summarize(self, previous: str | None, messages: Sequence[tuple[MessageRole, str]]) -> str:
        return "存不下\x00的摘要"


async def test_failed_summary_save_still_gives_one_normal_reply_and_keeps_the_cursor(
    session_factory: SessionFactory, actions: ActionService, conversation_id: int, users: dict[str, User]
) -> None:
    for index in range(1, 5):
        await add_round(session_factory, conversation_id, f"第 {index} 个问题")
    model = ScriptedChatModel(respond=always(AIMessage("好的")))
    memory = ConversationMemory(session_factory, actions, summarizer=UnsavableSummarizer(), limits=SMALL)
    service = ConversationService(session_factory, ReplyAgent(model, FakeToolbox([])), actions, memory)

    events = [event async for event in await service.send_message(users["alice"].id, conversation_id, "当前问题")]

    assert [event.type for event in events] == ["message.accepted", "message.delta", "message.delta", "message.done"]
    async with session_factory() as session:
        sent = await session.scalar(select(func.count()).where(Message.content == "当前问题"))
    assert sent == 1
    assert await summaries_of(session_factory, conversation_id) == []  # 游标没有前移
    assert questions_in(model.calls[0]) == ["第 3 个问题", "第 4 个问题", "当前问题"]  # 退回到按预算裁剪


async def test_someone_elses_conversation_cannot_be_read(
    memory: ConversationMemory, session_factory: SessionFactory, conversation_id: int, users: dict[str, User]
) -> None:
    await add_message(session_factory, conversation_id, "user", "alice 的问题")

    with pytest.raises(ConversationNotFoundError):
        await memory.context_for(users["bob"].id, conversation_id)


def test_default_limits_hold_the_longest_message_the_api_accepts() -> None:
    assert ContextLimits().max_question_tokens >= estimate_tokens("问" * MAX_MESSAGE_LENGTH)
    with pytest.raises(ValueError):
        ContextLimits(budget_tokens=4500)
