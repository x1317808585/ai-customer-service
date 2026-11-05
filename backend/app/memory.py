"""会话记忆：每一轮回复前，组装交给模型的上下文。

对外只有 ConversationMemory.context_for 一个入口。它保证：
- 只读取当前用户自己的会话和长期偏好；
- 返回的上下文不超过预算，并且一定完整包含当前这个问题；
- 确认卡片一律按操作的最新状态描述（包括已经被摘要覆盖的卡片）。

做法：消息按“轮”划分，一轮从一条用户消息开始，到下一条用户消息之前为止。
摘要之后的消息太多（超过轮数或 Token 阈值）时，先把较早的完整轮次连同旧摘要合并成新摘要，
保存成功后再往后挪“摘要覆盖到哪条消息”的游标；然后按预算从最近的轮次往前取原文。
摘要保存失败不影响这次回复，只是这一轮退回到按预算裁剪。
"""

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.actions import ActionService
from app.models import Conversation, ConversationSummary, Message, MessageRole, PendingAction
from app.preferences import Preferences, preferences_max_tokens

logger = logging.getLogger(__name__)

MESSAGE_OVERHEAD_TOKENS = 4  # 每条消息除正文外的固定开销（角色等）
ACTION_LINE_MAX_CHARS = 80  # 摘要里每张卡片的状态说明最多这么多字
SUMMARY_FRAME = "早前对话的摘要（这些消息不再逐条提供）：\n{summary}"
ACTIONS_FRAME = "\n其中确认卡片的最新状态：\n{lines}"

_HAN = re.compile("[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


class ConversationNotFoundError(Exception):
    """会话不存在，或者不属于当前用户。两种情况对外表现一致，不泄露别人的会话是否存在。"""


def estimate_tokens(text: str) -> int:
    """离线估算 Token 数：中文和全角符号每个算 1 个，其他字符每 4 个算 1 个。宁可多算，不会少算太多。

    只用来做预算和裁剪，不保证等于真实模型的计数；接入真实模型时换成它自己的分词器。
    """
    wide = len(_HAN.findall(text))
    return wide + math.ceil((len(text) - wide) / 4)


@dataclass(frozen=True)
class ContextLimits:
    """上下文的上限（都不含系统提示词和本轮的工具调用）。"""

    budget_tokens: int = 8000  # 长期偏好 + 摘要 + 历史原文 + 当前问题，合计不超过这么多
    summarize_at_tokens: int = 6000  # 摘要之后的原文（连同已有摘要）超过这么多就重新摘要
    keep_rounds: int = 10  # 最多保留最近几轮原文（含当前这一轮）
    max_question_tokens: int = 4000  # 一条用户消息最多多少 Token（与接口的 MAX_MESSAGE_LENGTH 一致）
    summary_max_tokens: int = 800  # 摘要正文的上限
    summary_max_actions: int = 10  # 摘要里最多记几张卡片

    def __post_init__(self) -> None:
        # 当前问题必须能和长期偏好、摘要一起放进去，否则“不超预算”和“完整保留当前问题”无法同时成立
        longest_question = self.max_question_tokens + MESSAGE_OVERHEAD_TOKENS
        if self.budget_tokens < longest_question + self.reserved_tokens:
            raise ValueError("budget_tokens 放不下最长的问题加上长期偏好和摘要")
        if self.summarize_at_tokens - self.reserved_tokens < longest_question:
            raise ValueError("summarize_at_tokens 太小：摘要之后放不下最长的问题")
        if self.summarize_at_tokens > self.budget_tokens:
            raise ValueError("summarize_at_tokens 不能超过 budget_tokens")

    @property
    def reserved_tokens(self) -> int:
        """长期偏好块和摘要部分（正文、卡片状态、固定说明）最多占用的 Token。"""
        preferences = preferences_max_tokens() + MESSAGE_OVERHEAD_TOKENS
        frame = estimate_tokens(SUMMARY_FRAME + ACTIONS_FRAME) + MESSAGE_OVERHEAD_TOKENS
        return preferences + frame + self.summary_max_tokens + self.summary_max_actions * (ACTION_LINE_MAX_CHARS + 1)


class Summarizer(Protocol):
    async def summarize(self, previous: str | None, messages: Sequence[tuple[MessageRole, str]]) -> str:
        """把旧摘要和一段较早的消息（角色，正文）合并成新摘要。"""
        ...


@dataclass(frozen=True)
class _Summary:
    covers_through_message_id: int
    content: str
    action_ids: list[int]


class ConversationMemory:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        actions: ActionService,
        summarizer: Summarizer | None = None,
        limits: ContextLimits | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._actions = actions
        self._summarizer = summarizer or OfflineSummarizer()
        self._limits = limits or ContextLimits()
        self._preferences = Preferences(session_factory)

    async def context_for(self, user_id: int, conversation_id: int) -> list[BaseMessage]:
        """交给模型的上下文：（长期偏好）+（摘要）+ 最近几轮原文，最后一条是当前问题。

        调用前当前问题必须已经保存。会话不属于这个用户时抛出 ConversationNotFoundError。
        """
        async with self._session_factory() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise ConversationNotFoundError
            summary = await _latest_summary(session, conversation_id)
            rounds = _rounds(await _messages_after(session, conversation_id, summary))

        keep = self._rounds_to_keep(rounds)
        if self._needs_summary(summary, rounds) and keep < len(rounds):
            renewed = await self._summarize(conversation_id, summary, rounds[:-keep])
            if renewed is not None and renewed is not summary:
                summary, rounds = renewed, rounds[-keep:]
            # 摘要没保存成功时，游标不动，下面按预算从全部原文里取最近的轮次

        async with self._session_factory() as session:
            preferences = await self._preferences.context_text(session, user_id)
            summary_message = await self._summary_message(session, summary)
        fixed: list[BaseMessage] = []
        if preferences:
            fixed.append(SystemMessage(preferences))
        if summary_message:
            fixed.append(summary_message)
        recent = self._fit(rounds, self._limits.budget_tokens - _tokens(fixed))
        return fixed + [self._to_langchain(message) for round_ in recent for message in round_]

    def _needs_summary(self, summary: _Summary | None, rounds: list[list[Message]]) -> bool:
        if len(rounds) <= 1:
            return False
        size = (estimate_tokens(summary.content) if summary else 0) + sum(map(self._round_tokens, rounds))
        return len(rounds) > self._limits.keep_rounds or size > self._limits.summarize_at_tokens

    def _rounds_to_keep(self, rounds: list[list[Message]]) -> int:
        """重新摘要后保留几轮原文：最近的若干轮，不超过轮数，也不超过“阈值减去长期偏好和摘要的占用”。至少保留当前这一轮。"""
        room = self._limits.summarize_at_tokens - self._limits.reserved_tokens
        keep, used = 0, 0
        for round_ in reversed(rounds):
            used += self._round_tokens(round_)
            if keep >= 1 and (keep >= self._limits.keep_rounds or used > room):
                break
            keep += 1
        return keep

    async def _summarize(
        self, conversation_id: int, previous: _Summary | None, old_rounds: list[list[Message]]
    ) -> _Summary | None:
        """把旧摘要和较早的轮次合并成新摘要，在单独的事务里保存。失败时记日志，返回原来的摘要。"""
        messages = [message for round_ in old_rounds for message in round_]
        try:
            content = await self._summarizer.summarize(
                previous.content if previous else None,
                [(message.role, self._text_of(message)) for message in messages],
            )
            new_ids = [message.action.id for message in messages if message.action is not None]
            summary = _Summary(
                covers_through_message_id=messages[-1].id,
                content=_cut(content, self._limits.summary_max_tokens),
                action_ids=((previous.action_ids if previous else []) + new_ids)[-self._limits.summary_max_actions :],
            )
            async with self._session_factory.begin() as session:
                session.add(
                    ConversationSummary(
                        conversation_id=conversation_id,
                        covers_through_message_id=summary.covers_through_message_id,
                        content=summary.content,
                        action_ids=summary.action_ids,
                    )
                )
        except Exception:
            logger.warning("会话 %s 的摘要没有保存，这一轮只按预算裁剪", conversation_id, exc_info=True)
            return previous
        return summary

    async def _summary_message(self, session: AsyncSession, summary: _Summary | None) -> SystemMessage | None:
        """摘要正文，加上它记下的确认卡片的最新状态（每轮重新读取）。"""
        if summary is None:
            return None
        text = SUMMARY_FRAME.format(summary=summary.content)
        if summary.action_ids:
            actions = await session.scalars(
                select(PendingAction).where(PendingAction.id.in_(summary.action_ids)).order_by(PendingAction.id)
            )
            lines = "\n".join(self._actions.brief(action)[:ACTION_LINE_MAX_CHARS] for action in actions)
            text += ACTIONS_FRAME.format(lines=lines)
        return SystemMessage(text)

    def _fit(self, rounds: list[list[Message]], room: int) -> list[list[Message]]:
        """从最近的一轮往前取，不超过轮数和 Token 预算。当前这一轮无论如何都保留。"""
        kept: list[list[Message]] = []
        used = 0
        for round_ in reversed(rounds):
            used += self._round_tokens(round_)
            if kept and (len(kept) >= self._limits.keep_rounds or used > room):
                break
            kept.insert(0, round_)
        return kept

    def _round_tokens(self, round_: list[Message]) -> int:
        return _tokens([self._to_langchain(message) for message in round_])

    def _text_of(self, message: Message) -> str:
        """确认卡片按操作的当前状态描述，模型据此回答“提交成功了吗”。"""
        return self._actions.describe(message.action) if message.action is not None else message.content

    def _to_langchain(self, message: Message) -> BaseMessage:
        if message.role == "user":
            return HumanMessage(message.content)
        return AIMessage(self._text_of(message))


class OfflineSummarizer:
    """不接模型时用的摘要器：按固定规则抽出要点，结果确定，方便测试。真实模型的摘要器以后按同一个接口接入。

    要点有三项：用户问过的问题（最近几个）、提到过的订单号、提到过的订单项编号。旧摘要里的要点会保留下来。
    """

    QUESTIONS = "用户问过"
    ORDERS = "提到的订单号"
    ITEMS = "提到的订单项"
    MAX_QUESTIONS = 5
    MAX_IDS = 10
    QUESTION_CHARS = 30

    async def summarize(self, previous: str | None, messages: Sequence[tuple[MessageRole, str]]) -> str:
        facts = self._parse(previous or "")
        for role, text in messages:
            if role == "user":
                question = re.sub(r"\s+", " ", text).replace("；", "，").strip()[: self.QUESTION_CHARS]
                facts[self.QUESTIONS].append(question)
            facts[self.ORDERS] += re.findall(r"订单(?!项)号?\s*#?(\d+)", text)
            facts[self.ITEMS] += re.findall(r"订单项\s*#?(\d+)", text)

        limits = {self.QUESTIONS: self.MAX_QUESTIONS, self.ORDERS: self.MAX_IDS, self.ITEMS: self.MAX_IDS}
        lines = []
        for label, values in facts.items():
            unique = list(dict.fromkeys(value for value in values if value))[-limits[label] :]
            if unique:
                separator = "；" if label == self.QUESTIONS else "、"
                lines.append(f"{label}：{separator.join(unique)}")
        return "\n".join(lines)

    def _parse(self, text: str) -> dict[str, list[str]]:
        facts: dict[str, list[str]] = {self.QUESTIONS: [], self.ORDERS: [], self.ITEMS: []}
        for line in text.splitlines():
            label, _, values = line.partition("：")
            if label in facts:
                facts[label] = [value for value in re.split("[；、]", values) if value]
        return facts


async def _latest_summary(session: AsyncSession, conversation_id: int) -> _Summary | None:
    row = await session.scalar(
        select(ConversationSummary)
        .where(ConversationSummary.conversation_id == conversation_id)
        .order_by(ConversationSummary.id.desc())
        .limit(1)
    )
    if row is None:
        return None
    return _Summary(row.covers_through_message_id, row.content, list(row.action_ids))


async def _messages_after(session: AsyncSession, conversation_id: int, summary: _Summary | None) -> list[Message]:
    """摘要游标之后的全部消息（没有摘要时是整个会话），按顺序。"""
    query = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .options(selectinload(Message.action))
        .order_by(Message.id)
    )
    if summary is not None:
        query = query.where(Message.id > summary.covers_through_message_id)
    return list(await session.scalars(query))


def _rounds(messages: list[Message]) -> list[list[Message]]:
    """按用户消息分轮：每条用户消息开始新的一轮。"""
    rounds: list[list[Message]] = []
    for message in messages:
        if message.role == "user" or not rounds:
            rounds.append([])
        rounds[-1].append(message)
    return rounds


def _tokens(messages: Sequence[BaseMessage]) -> int:
    return sum(estimate_tokens(message.text) + MESSAGE_OVERHEAD_TOKENS for message in messages)


def _cut(text: str, max_tokens: int) -> str:
    """截到不超过 max_tokens（从末尾去掉）。"""
    while estimate_tokens(text) > max_tokens:
        text = text[: -max(1, len(text) // 10)]
    return text
