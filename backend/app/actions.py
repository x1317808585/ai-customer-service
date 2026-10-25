"""待确认操作：助手提议的业务写操作，要用户在页面上点“确认”才执行。

确认接口是业务写入的唯一入口。Agent 只能通过 MCP 查询和预览，手里没有任何执行写操作的工具。
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, PositiveInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.business.returns import ReturnPreview, Returns
from app.models import ActionKind, Message, PendingAction

ACTION_LIFETIME = timedelta(minutes=10)

# 对外显示的状态：数据库里的四种状态，加上“待确认但已超时”算出来的 expired
ActionState = Literal["pending", "executed", "cancelled", "failed", "expired"]

# 写给模型看的状态（下一轮对话时，模型据此回答“提交成功了吗”）
_STATE_TEXT: dict[ActionState, str] = {
    "pending": "等待顾客确认",
    "executed": "已提交",
    "cancelled": "顾客已取消",
    "failed": "提交失败",
    "expired": "已过期，未提交",
}

# 写给用户看的：为什么不能再确认或取消
_NOT_CONFIRMABLE_TEXT: dict[ActionState, str] = {
    "pending": "这项操作还在等待确认",
    "executed": "这项操作已经提交过了",
    "failed": "这项操作已经处理过了",
    "cancelled": "这项操作已经取消了",
    "expired": "这项操作已经过期，请重新发起",
}


class ActionNotFoundError(Exception):
    """操作不存在，或者不属于当前用户。两种情况对外表现一致。"""


class ActionNotConfirmableError(Exception):
    """操作已经不能确认或取消了（已取消、已过期、已处理）。带上它的当前状态，前端据此更新卡片。"""

    def __init__(self, action: "ActionView") -> None:
        super().__init__(_NOT_CONFIRMABLE_TEXT[action.status])
        self.action = action


class ReturnRequestArguments(BaseModel):
    """退货申请的执行参数。多余的字段直接拒绝。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_item_id: int
    quantity: PositiveInt
    reason: str


class ActionView(BaseModel):
    """对外返回的操作状态。确认卡片完全按它显示。"""

    model_config = ConfigDict(frozen=True)

    id: int
    kind: ActionKind
    status: ActionState
    preview: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    expires_at: datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


class ActionService:
    """创建、查询、确认、取消待确认操作。确认在一个事务里完成：锁操作 → 执行 → 记结果。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        returns: Returns,
        clock: Callable[[], datetime] = utc_now,
        lifetime: timedelta = ACTION_LIFETIME,
    ) -> None:
        self._session_factory = session_factory
        self._returns = returns
        self._clock = clock
        self._lifetime = lifetime

    async def propose_in(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        conversation_id: int,
        source_message_id: int,
        kind: ActionKind,
        arguments: dict[str, Any],
        preview: dict[str, Any],
    ) -> PendingAction:
        """在调用方的事务里创建确认卡片消息和待确认操作（只写入，不提交）。"""
        checked_arguments = ReturnRequestArguments.model_validate(arguments).model_dump()
        checked_preview = ReturnPreview.model_validate(preview).model_dump()
        card = Message(conversation_id=conversation_id, role="assistant", content="")
        session.add(card)
        await session.flush()  # 拿到卡片消息的 id
        action = PendingAction(
            user_id=user_id,
            conversation_id=conversation_id,
            source_message_id=source_message_id,
            card_message_id=card.id,
            kind=kind,
            arguments=checked_arguments,
            preview=checked_preview,
            status="pending",
            expires_at=self._clock() + self._lifetime,
        )
        session.add(action)
        await session.flush()
        card.content = self.describe(action)
        return action

    async def get(self, user_id: int, action_id: int) -> "ActionView":
        async with self._session_factory() as session:
            action = await session.scalar(
                select(PendingAction).where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            )
            if action is None:
                raise ActionNotFoundError
            return self.view(action)

    async def confirm(self, user_id: int, action_id: int) -> "ActionView":
        """确认并执行。重复确认返回同一个结果；业务规则拒绝时记为 failed 并正常返回。

        系统错误（比如数据库断开）不捕获：整个事务回滚，操作仍是 pending，可以重试。
        """
        async with self._session_factory() as session, session.begin():
            action = await self._lock(session, user_id, action_id)
            if action.status in ("executed", "failed"):
                return self.view(action)
            self._ensure_still_pending(action)

            try:
                # 保存点：业务写入失败时只撤回这一部分，下面记下的 failed 状态照常提交
                async with session.begin_nested():
                    result = await self._execute(session, action)
            except BusinessError as error:
                action.status, action.error = "failed", str(error)
            else:
                action.status, action.result = "executed", result
            action.resolved_at = self._clock()
            return self.view(action)

    async def cancel(self, user_id: int, action_id: int) -> "ActionView":
        async with self._session_factory() as session, session.begin():
            action = await self._lock(session, user_id, action_id)
            self._ensure_still_pending(action)
            action.status, action.resolved_at = "cancelled", self._clock()
            return self.view(action)

    def view(self, action: PendingAction) -> "ActionView":
        return ActionView(
            id=action.id,
            kind=action.kind,
            status=self._state_of(action),
            preview=action.preview,
            result=action.result,
            error=action.error,
            expires_at=action.expires_at,
        )

    def describe(self, action: PendingAction) -> str:
        """给模型看的一句话，按当前状态生成。下一轮对话时模型据此回答“提交成功了吗”。"""
        preview = ReturnPreview.model_validate(action.preview)
        state = self._state_of(action)
        detail = _STATE_TEXT[state]
        if state == "executed" and action.result:
            detail += f"，退货单号 {action.result['return_id']}"
        elif state == "failed" and action.error:
            detail += f"：{action.error}"
        return (
            f"（退货申请：订单 {preview.order_id} 的订单项 {preview.order_item_id}「{preview.product_name}」"
            f" × {preview.quantity}，退款 {format_cents(preview.refund_cents)} 元，原因：{preview.reason}。"
            f"状态：{detail}）"
        )

    async def _lock(self, session: AsyncSession, user_id: int, action_id: int) -> PendingAction:
        """锁住这条操作：确认、取消、重复点击都在这里排队。先查归属，不是本人的一律当作不存在。"""
        action = await session.scalar(
            select(PendingAction)
            .where(PendingAction.id == action_id, PendingAction.user_id == user_id)
            .with_for_update()
        )
        if action is None:
            raise ActionNotFoundError
        return action

    def _ensure_still_pending(self, action: PendingAction) -> None:
        if self._state_of(action) != "pending":
            raise ActionNotConfirmableError(self.view(action))

    def _state_of(self, action: PendingAction) -> ActionState:
        """过期以服务器时间为准；已经处理过的操作不会因为超过 10 分钟而变成“已过期”。"""
        if action.status == "pending" and self._clock() > action.expires_at:
            return "expired"
        return action.status

    async def _execute(self, session: AsyncSession, action: PendingAction) -> dict[str, Any]:
        """按保存的参数执行。会先锁住业务数据、按最新数据重新计算，和用户确认过的预览不一致就拒绝。"""
        arguments = ReturnRequestArguments.model_validate(action.arguments)
        created = await self._returns.request_return_in(
            session,
            action.user_id,
            arguments.order_item_id,
            arguments.quantity,
            arguments.reason,
            expected=ReturnPreview.model_validate(action.preview),
        )
        return {"return_id": created.id}


def format_cents(cents: int) -> str:
    """59900 → "599.00"。用整数运算，不经过小数。"""
    return f"{cents // 100}.{cents % 100:02d}"
