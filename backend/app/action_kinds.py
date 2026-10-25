"""每种待确认操作的定义：参数、预览、怎么描述、怎么执行。

新增一种操作：在这里加一个定义，再改数据库里 kind 的约束（要手写迁移）。确认服务本身不用改。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, PositiveInt
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.returns import ReturnCancellationPreview, ReturnPreview, Returns
from app.business.tickets import TicketPreview, Tickets
from app.models import PendingAction

Execute = Callable[[AsyncSession, PendingAction], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ActionKindSpec:
    """一种待确认操作。

    title：卡片标题，也用在给模型看的说明里。
    arguments / preview：执行参数和预览的格式，创建和执行时都按它校验。
    describe：把预览写成一句话（给模型看）。
    done_text：执行成功后的说明，比如“已提交，退货单号 3”。
    execute：在确认服务给的事务里执行，返回要保存的结果。
    """

    title: str
    arguments: type[BaseModel]
    preview: type[BaseModel]
    describe: Callable[[Any], str]
    done_text: Callable[[dict[str, Any]], str]
    execute: Execute


class ReturnRequestArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    order_item_id: int
    quantity: PositiveInt
    reason: str


class ReturnCancellationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    return_id: int


class TicketArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    order_id: int | None = None


def build_action_kinds(returns: Returns, tickets: Tickets) -> dict[str, ActionKindSpec]:
    """创建全部操作类型的定义。执行函数用到的业务对象从这里传进去。"""

    async def request_return(session: AsyncSession, action: PendingAction) -> dict[str, Any]:
        # 先锁住订单项、按最新数据重新计算，和用户确认过的预览不一致就拒绝
        arguments = ReturnRequestArguments.model_validate(action.arguments)
        created = await returns.request_return_in(
            session,
            action.user_id,
            arguments.order_item_id,
            arguments.quantity,
            arguments.reason,
            expected=ReturnPreview.model_validate(action.preview),
        )
        return {"return_id": created.id}

    async def cancel_return(session: AsyncSession, action: PendingAction) -> dict[str, Any]:
        # 退货单的内容创建后不会变，锁住它、确认还在处理中就够了，不需要和预览比对
        arguments = ReturnCancellationArguments.model_validate(action.arguments)
        cancelled = await returns.cancel_return_in(session, action.user_id, arguments.return_id)
        return {"return_id": cancelled.id}

    async def open_ticket(session: AsyncSession, action: PendingAction) -> dict[str, Any]:
        # 来源会话取自待确认操作本身，不来自模型的参数
        arguments = TicketArguments.model_validate(action.arguments)
        ticket = await tickets.open_ticket_in(
            session, action.user_id, arguments.summary, arguments.order_id, conversation_id=action.conversation_id
        )
        return {"ticket_id": ticket.id}

    return {
        "return_request": ActionKindSpec(
            title="退货申请",
            arguments=ReturnRequestArguments,
            preview=ReturnPreview,
            describe=_describe_return_request,
            done_text=lambda result: f"已提交，退货单号 {result['return_id']}",
            execute=request_return,
        ),
        "return_cancellation": ActionKindSpec(
            title="撤回退货申请",
            arguments=ReturnCancellationArguments,
            preview=ReturnCancellationPreview,
            describe=_describe_return_cancellation,
            done_text=lambda result: f"已撤回退货申请 {result['return_id']}",
            execute=cancel_return,
        ),
        "ticket": ActionKindSpec(
            title="转人工",
            arguments=TicketArguments,
            preview=TicketPreview,
            describe=_describe_ticket,
            done_text=lambda result: f"已创建工单 #{result['ticket_id']}，等待人工处理",
            execute=open_ticket,
        ),
    }


def _describe_return_request(preview: ReturnPreview) -> str:
    return (
        f"订单 {preview.order_id} 的订单项 {preview.order_item_id}「{preview.product_name}」 × {preview.quantity}，"
        f"退款 {format_cents(preview.refund_cents)} 元，原因：{preview.reason}"
    )


def _describe_return_cancellation(preview: ReturnCancellationPreview) -> str:
    return (
        f"退货申请 {preview.return_id}（订单 {preview.order_id}「{preview.product_name}」 × {preview.quantity}，"
        f"退款 {format_cents(preview.refund_cents)} 元）"
    )


def _describe_ticket(preview: TicketPreview) -> str:
    order = f"订单 {preview.order_id}，" if preview.order_id is not None else ""
    return f"{order}问题摘要：{preview.summary}"


def format_cents(cents: int) -> str:
    """59900 → "599.00"。用整数运算，不经过小数。"""
    return f"{cents // 100}.{cents % 100:02d}"
