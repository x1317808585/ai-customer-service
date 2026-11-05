"""长期偏好：顾客明确说过的、和以后服务有关的稳定偏好，按用户保存，每个会话都能读到。

只收下面几个固定字段，值也只能是列出的几种。手机号、地址、订单号这类内容根本存不进来。
偏好只从顾客新发的消息里提取，不从摘要或聊天记录里重建，所以删掉的偏好不会被带回来。
同一个字段只保留一条，顾客改口时以最新的说法为准。
"""

import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import UserMemory


@dataclass(frozen=True)
class PreferenceField:
    label: str
    values: tuple[str, ...]


# 字段 → 名称和可选值。值按长度从长到短排，保证“半入耳式”不会被当成“入耳式”
FIELDS: dict[str, PreferenceField] = {
    "earphone_fit": PreferenceField("耳机佩戴方式", ("半入耳式", "入耳式", "头戴式")),
    "keyboard_switch": PreferenceField("键盘轴体", ("茶轴", "红轴", "青轴", "黑轴")),
    "reply_detail": PreferenceField("回复详略", ("简洁", "详细")),
}

PREFERENCES_FRAME = "顾客的长期偏好（仅供参考，商品、订单等业务信息以实时查询为准）：{items}"

# 顾客说自己的偏好：“我”后面直接跟“喜欢、偏好、习惯、想要、要、用”（中间可以有“更、比较”这类词），
# 再接值，中间只允许“戴、用、买、的、那种”这类字。“我不喜欢”“我朋友喜欢”“我要问一下入耳式……”都不符合
_I_PREFER = r"我(?:更|比较|还是|一直|最|平时)?(?:喜欢|偏好|习惯|想要|要|用)(?:戴|用|买|选|的|那种|这种|款)*"
_NEGATION = re.compile("[不别没非]")
_CLAUSE = re.compile("[，,。.！!？?；;\n]")
_EARPHONE_ALIASES = {"半入耳": "半入耳式", "入耳": "入耳式", "头戴": "头戴式"}
_REPLY = re.compile(r"(?:回复|回答)")
# 顾客自己提的要求：分句以“以后、请”这类词开头，或者直接说“我喜欢、我希望”。“我朋友希望以后……”不算
_WISH = re.compile(r"^\s*(?:以后|之后|今后|请|麻烦)|我(?:喜欢|希望|想要)")
_CONCISE = re.compile(r"简洁|简短|简单|短一?点")
_DETAILED = re.compile(r"详细|具体")


class MemoryView(BaseModel):
    """对外返回的一条偏好。"""

    model_config = ConfigDict(frozen=True)

    id: int
    field: str
    label: str
    value: str
    updated_at: datetime


def extract_preferences(text: str) -> dict[str, str]:
    """从一条顾客消息里提取明确的偏好，返回 {字段: 值}。没有就返回空。同一字段说了多次，以最后一次为准。"""
    found: dict[str, str] = {}
    for clause in _CLAUSE.split(text):
        if earphone := _stated(clause, r"(半入耳式?|入耳式?|头戴式?)"):
            found["earphone_fit"] = _EARPHONE_ALIASES.get(earphone, earphone)
        if switch := _stated(clause, "(" + "|".join(FIELDS["keyboard_switch"].values) + ")"):
            found["keyboard_switch"] = switch
        if detail := _reply_detail(clause):
            found["reply_detail"] = detail
    return found


def _stated(clause: str, values: str) -> str | None:
    """“我更喜欢入耳式”这类说法里的值。一个分句里说了几次（“我喜欢红轴但我更喜欢茶轴”），取最后一次。"""
    matches = re.findall(_I_PREFER + values, clause)
    return matches[-1] if matches else None


def _reply_detail(clause: str) -> str | None:
    """“以后回复简洁点”“我喜欢详细的回答”这类顾客自己提的要求。
    带否定词的（比如“回复不要太详细”）不猜，跳过。"""
    if not (_REPLY.search(clause) and _WISH.search(clause)) or _NEGATION.search(clause):
        return None
    if _CONCISE.search(clause):
        return "简洁"
    if _DETAILED.search(clause):
        return "详细"
    return None


def preferences_max_tokens() -> int:
    """长期偏好块最多占多少字（按每个字一个 Token 估算，用来给上下文预算留位置）。"""
    items = "；".join(f"{field.label}={max(field.values, key=len)}" for field in FIELDS.values())
    return len(PREFERENCES_FRAME.format(items=items))


class Preferences:
    """长期偏好的读写。所有方法都只针对传入的用户。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def remember_in(self, session: AsyncSession, user_id: int, message_id: int, text: str) -> None:
        """在调用方的事务里，从这条消息提取偏好并保存（同一字段覆盖旧值）。"""
        for field, value in extract_preferences(text).items():
            statement = insert(UserMemory).values(
                user_id=user_id, field=field, value=value, source_message_id=message_id
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[UserMemory.user_id, UserMemory.field],
                    set_={"value": value, "source_message_id": message_id, "updated_at": func.now()},
                )
            )

    async def list_for(self, user_id: int) -> list[MemoryView]:
        async with self._session_factory() as session:
            rows = await session.scalars(select(UserMemory).where(UserMemory.user_id == user_id).order_by(UserMemory.id))
            return [_view(row) for row in rows]

    async def delete(self, user_id: int, memory_id: int) -> bool:
        """删除自己的一条偏好。不存在或不是自己的，返回 False，什么都不改。"""
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
            )
            return result.rowcount > 0

    async def context_text(self, session: AsyncSession, user_id: int) -> str | None:
        """交给模型的长期偏好块，比如“……：耳机佩戴方式=入耳式；回复详略=简洁”。没有偏好时返回 None。"""
        rows = await session.scalars(select(UserMemory).where(UserMemory.user_id == user_id))
        known = {row.field: row.value for row in rows}
        items = [f"{field.label}={known[key]}" for key, field in FIELDS.items() if key in known]
        return PREFERENCES_FRAME.format(items="；".join(items)) if items else None


def _view(row: UserMemory) -> MemoryView:
    return MemoryView(id=row.id, field=row.field, label=FIELDS[row.field].label, value=row.value, updated_at=row.updated_at)
