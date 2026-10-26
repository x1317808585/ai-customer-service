import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.action_kinds import build_action_kinds
from app.actions import (
    ACTION_LIFETIME,
    ActionNotConfirmableError,
    ActionNotFoundError,
    ActionService,
    UnknownActionKindError,
)
from app.business.returns import RETURN_WINDOW, ReturnPreviewChangedError, Returns
from app.business.tickets import Tickets
from app.models import Conversation, Message, Order, PendingAction, Product, ReturnRequest, Ticket, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

DELIVERED_AT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class Clock:
    """可以拨动的时钟：退货规则和待确认操作共用它，测试里能模拟“10 分钟后”。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock(DELIVERED_AT + timedelta(days=1))


@pytest.fixture
def returns(session_factory: SessionFactory, clock: Clock) -> Returns:
    return Returns(session_factory, clock=clock)


def action_service(session_factory: SessionFactory, returns: Returns, clock: Clock) -> ActionService:
    """用给定的 Returns 组装确认服务。测试可以换成会出错的 Returns。"""
    return ActionService(session_factory, build_action_kinds(returns, Tickets(session_factory)), clock=clock)


@pytest.fixture
def actions(session_factory: SessionFactory, returns: Returns, clock: Clock) -> ActionService:
    return action_service(session_factory, returns, clock)


Propose = Callable[..., Awaitable[PendingAction]]
ProposeReturn = Callable[..., Awaitable[int]]


@pytest.fixture
def propose(session_factory: SessionFactory, actions: ActionService) -> Propose:
    """模拟一次回复里助手发起了提议：建会话、用户消息，再创建待确认操作。"""

    async def _propose(user: User, kind: str, arguments: dict[str, Any], preview: dict[str, Any]) -> PendingAction:
        async with session_factory() as session, session.begin():
            conversation = Conversation(user_id=user.id, title="咨询")
            session.add(conversation)
            await session.flush()
            question = Message(conversation_id=conversation.id, role="user", content="（顾客的话）")
            session.add(question)
            await session.flush()
            return await actions.propose_in(
                session,
                user_id=user.id,
                conversation_id=conversation.id,
                source_message_id=question.id,
                kind=kind,  # type: ignore[arg-type]
                arguments=arguments,
                preview=preview,
            )

    return _propose


@pytest.fixture
def propose_return(returns: Returns, propose: Propose) -> ProposeReturn:
    """发起退货提议（预览由业务层算出），返回操作 id。"""

    async def _propose_return(user: User, order_item_id: int, quantity: int = 1) -> int:
        preview = await returns.preview_return(user.id, order_item_id, quantity, "坏了")
        arguments = {"order_item_id": order_item_id, "quantity": quantity, "reason": "坏了"}
        action = await propose(user, "return_request", arguments, preview.model_dump())
        return action.id

    return _propose_return


@pytest.fixture
async def earbuds_item(users: dict[str, User], add_product: AddProduct, add_order: AddOrder) -> int:
    """alice 的一个已签收订单项：SoundPods 降噪耳机 × 1。"""
    product = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, product, delivered_at=DELIVERED_AT)
    return order.items[0].id


async def return_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(ReturnRequest))


# ---------- 确认 ----------


async def test_confirm_submits_the_saved_request(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    confirmed = await actions.confirm(users["alice"].id, action_id)

    assert confirmed.status == "executed"
    async with session_factory() as session:
        created = await session.scalar(select(ReturnRequest))
    assert created is not None
    assert confirmed.result == {"return_id": created.id}
    assert (created.order_item_id, created.quantity, created.refund_cents) == (earbuds_item, 1, 59900)


async def test_concurrent_confirms_submit_only_once(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    # 先锁住这条操作，让 10 个确认请求堵在同一起跑线上再一起放行
    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(PendingAction).where(PendingAction.id == action_id).with_for_update())
        attempts = [asyncio.create_task(actions.confirm(users["alice"].id, action_id)) for _ in range(10)]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts)

    assert await return_count(session_factory) == 1
    assert len({result.model_dump_json() for result in results}) == 1  # 10 个请求得到同一个结果
    assert results[0].status == "executed"


async def test_confirm_and_cancel_at_the_same_time_only_one_wins(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(PendingAction).where(PendingAction.id == action_id).with_for_update())
        confirm = asyncio.create_task(actions.confirm(users["alice"].id, action_id))
        cancel = asyncio.create_task(actions.cancel(users["alice"].id, action_id))
        await asyncio.sleep(0.5)
    outcomes = await asyncio.gather(confirm, cancel, return_exceptions=True)

    final = await actions.get(users["alice"].id, action_id)
    losers = [outcome for outcome in outcomes if isinstance(outcome, ActionNotConfirmableError)]
    assert len(losers) == 1 and losers[0].action == final
    assert (final.status, await return_count(session_factory)) in [("executed", 1), ("cancelled", 0)]


async def test_confirming_again_after_a_lost_response_returns_the_same_result(
    actions: ActionService, propose_return: ProposeReturn, users: dict[str, User], earbuds_item: int, clock: Clock,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    first = await actions.confirm(users["alice"].id, action_id)  # 假设这个响应在网络上丢了

    clock.now += ACTION_LIFETIME + timedelta(hours=1)  # 而且用户过了很久才重试
    again = await actions.confirm(users["alice"].id, action_id)

    assert again == first
    assert again.status == "executed"
    assert await actions.get(users["alice"].id, action_id) == first
    assert await return_count(session_factory) == 1


async def test_business_rejection_is_recorded_as_failed_and_writes_nothing(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    clock: Clock,
    session_factory: SessionFactory,
) -> None:
    clock.now = DELIVERED_AT + RETURN_WINDOW  # 预览时还在退货期的最后一刻
    action_id = await propose_return(users["alice"], earbuds_item)
    clock.now += timedelta(minutes=1)  # 确认时已经过了退货期（但待确认操作还没过期）

    failed = await actions.confirm(users["alice"].id, action_id)
    again = await actions.confirm(users["alice"].id, action_id)

    assert failed.status == "failed"
    assert failed.error is not None and "退货期" in failed.error
    assert again == failed
    assert await return_count(session_factory) == 0


async def test_preview_that_no_longer_matches_is_refused(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    async with session_factory() as session, session.begin():
        action = await session.get_one(PendingAction, action_id)
        action.preview = {**action.preview, "refund_cents": 1}  # 模拟用户确认的内容和实际要执行的不一致

    result = await actions.confirm(users["alice"].id, action_id)

    assert (result.status, result.error) == ("failed", "退货内容和确认时不一致，请重新发起")
    assert await return_count(session_factory) == 0


class RejectingAfterWriteReturns(Returns):
    """先写入退货申请，然后被业务规则拒绝。用来证明业务写入都在保存点里，拒绝时会被撤回。"""

    async def request_return_in(self, session, *args, **kwargs):  # type: ignore[no-untyped-def]
        await super().request_return_in(session, *args, **kwargs)
        raise ReturnPreviewChangedError()


async def test_business_rejection_after_a_partial_write_undoes_the_write_but_keeps_failed(
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    clock: Clock,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    rejecting = action_service(session_factory, RejectingAfterWriteReturns(session_factory, clock=clock), clock)

    result = await rejecting.confirm(users["alice"].id, action_id)

    assert result.status == "failed"
    assert await return_count(session_factory) == 0


class CrashingReturns(Returns):
    """先写入退货申请，然后模拟系统故障（比如数据库连接断开）。"""

    async def request_return_in(self, session, *args, **kwargs):  # type: ignore[no-untyped-def]
        await super().request_return_in(session, *args, **kwargs)
        raise ConnectionResetError("数据库连接断开")


async def test_system_failure_rolls_everything_back_and_can_be_retried(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    clock: Clock,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    crashing = action_service(session_factory, CrashingReturns(session_factory, clock=clock), clock)

    with pytest.raises(ConnectionResetError):
        await crashing.confirm(users["alice"].id, action_id)

    assert (await actions.get(users["alice"].id, action_id)).status == "pending"
    assert await return_count(session_factory) == 0
    assert (await actions.confirm(users["alice"].id, action_id)).status == "executed"


# ---------- 不能确认的情况 ----------


async def test_expired_action_cannot_be_confirmed_or_cancelled(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    clock: Clock,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    clock.now += ACTION_LIFETIME + timedelta(seconds=1)

    with pytest.raises(ActionNotConfirmableError) as confirm_error:
        await actions.confirm(users["alice"].id, action_id)
    with pytest.raises(ActionNotConfirmableError):
        await actions.cancel(users["alice"].id, action_id)

    assert confirm_error.value.action.status == "expired"
    assert await return_count(session_factory) == 0


async def test_cancelled_action_cannot_be_confirmed(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    cancelled = await actions.cancel(users["alice"].id, action_id)

    with pytest.raises(ActionNotConfirmableError) as error:
        await actions.confirm(users["alice"].id, action_id)

    assert cancelled.status == "cancelled"
    assert error.value.action.status == "cancelled"
    assert await return_count(session_factory) == 0


async def test_someone_elses_action_is_not_found(
    actions: ActionService, propose_return: ProposeReturn, users: dict[str, User], earbuds_item: int
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    for attempt in (actions.get, actions.confirm, actions.cancel):
        with pytest.raises(ActionNotFoundError):
            await attempt(users["bob"].id, action_id)


# ---------- 创建 ----------


async def test_only_one_action_per_source_message(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    async with session_factory() as session:  # 不提交，会话结束时自动回滚
        existing = await session.get_one(PendingAction, action_id)
        with pytest.raises(IntegrityError):
            await actions.propose_in(
                session,
                user_id=existing.user_id,
                conversation_id=existing.conversation_id,
                source_message_id=existing.source_message_id,
                kind="return_request",
                arguments=existing.arguments,
                preview=existing.preview,
            )


async def test_arguments_with_unexpected_fields_are_rejected(
    actions: ActionService, users: dict[str, User], session_factory: SessionFactory
) -> None:
    async with session_factory() as session, session.begin():
        with pytest.raises(ValidationError):
            await actions.propose_in(
                session,
                user_id=users["alice"].id,
                conversation_id=1,
                source_message_id=1,
                kind="return_request",
                arguments={"order_item_id": 1, "quantity": 1, "reason": "坏了", "refund_cents": 1},
                preview={},
            )


async def test_description_for_the_model_follows_the_current_state(
    actions: ActionService,
    propose_return: ProposeReturn,
    users: dict[str, User],
    earbuds_item: int,
    session_factory: SessionFactory,
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)
    confirmed = await actions.confirm(users["alice"].id, action_id)

    async with session_factory() as session:
        action = await session.get_one(PendingAction, action_id)
        card = await session.get_one(Message, action.card_message_id)

    assert "等待顾客确认" in card.content  # 卡片消息里存的是创建时的说明
    description = actions.describe(action)  # 给模型的说明按当前状态生成
    assert f"已提交，退货单号 {confirmed.result['return_id']}" in description
    assert "SoundPods 降噪耳机" in description and "599.00" in description


async def test_database_refuses_inconsistent_resolution_time(
    propose_return: ProposeReturn, users: dict[str, User], earbuds_item: int, session_factory: SessionFactory
) -> None:
    action_id = await propose_return(users["alice"], earbuds_item)

    async with session_factory() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(PendingAction).where(PendingAction.id == action_id).values(status="executed")
            )


# ---------- 撤回退货申请 ----------


@pytest.fixture
async def pending_return(returns: Returns, users: dict[str, User], earbuds_item: int) -> int:
    """alice 已经提交、还在处理中的退货申请，返回退货单号。"""
    created = await returns.request_return(users["alice"].id, earbuds_item, quantity=1, reason="坏了")
    return created.id


async def propose_cancellation(returns: Returns, propose: Propose, user: User, return_id: int) -> int:
    preview = await returns.preview_cancellation(user.id, return_id)
    action = await propose(user, "return_cancellation", {"return_id": return_id}, preview.model_dump())
    return action.id


async def return_status_and_stock(session_factory: SessionFactory, return_id: int) -> tuple[str, int]:
    async with session_factory() as session:
        request = await session.get_one(ReturnRequest, return_id)
        stock = await session.scalar(select(Product.stock).where(Product.name == "SoundPods 降噪耳机"))
        return request.status, stock


async def test_confirm_cancellation_cancels_the_return_and_keeps_stock(
    actions: ActionService,
    returns: Returns,
    propose: Propose,
    users: dict[str, User],
    pending_return: int,
    session_factory: SessionFactory,
) -> None:
    _status, stock_before = await return_status_and_stock(session_factory, pending_return)
    action_id = await propose_cancellation(returns, propose, users["alice"], pending_return)

    confirmed = await actions.confirm(users["alice"].id, action_id)
    again = await actions.confirm(users["alice"].id, action_id)

    assert (confirmed.status, confirmed.result) == ("executed", {"return_id": pending_return})
    assert again == confirmed
    assert await return_status_and_stock(session_factory, pending_return) == ("cancelled", stock_before)


async def test_cancellation_after_staff_completed_the_return_fails_and_restocks_once(
    actions: ActionService,
    returns: Returns,
    propose: Propose,
    users: dict[str, User],
    pending_return: int,
    session_factory: SessionFactory,
) -> None:
    _status, stock_before = await return_status_and_stock(session_factory, pending_return)
    action_id = await propose_cancellation(returns, propose, users["alice"], pending_return)
    await returns.complete_return(users["admin"].id, pending_return)  # 顾客还没点确认，管理员先完成了退货

    result = await actions.confirm(users["alice"].id, action_id)

    assert (result.status, result.error) == ("failed", "退货申请已经完成，不能再处理")
    assert await return_status_and_stock(session_factory, pending_return) == ("completed", stock_before + 1)


# ---------- 转人工 ----------


@pytest.fixture
def ticket_preview() -> dict[str, Any]:
    return {"order_id": None, "summary": "顾客反馈耳机左耳没声音，希望换货"}


async def test_confirm_ticket_creates_it_linked_to_the_conversation(
    actions: ActionService,
    ticket_preview: dict[str, Any],
    propose: Propose,
    users: dict[str, User],
    session_factory: SessionFactory,
) -> None:
    arguments = {"summary": "顾客反馈耳机左耳没声音，希望换货"}
    proposed = await propose(users["alice"], "ticket", arguments, ticket_preview)

    confirmed = await actions.confirm(users["alice"].id, proposed.id)

    async with session_factory() as session:
        ticket = await session.get_one(Ticket, confirmed.result["ticket_id"])
    assert confirmed.status == "executed"
    assert (ticket.user_id, ticket.conversation_id, ticket.summary, ticket.status) == (
        users["alice"].id,
        proposed.conversation_id,
        "顾客反馈耳机左耳没声音，希望换货",
        "open",
    )


async def test_ticket_arguments_cannot_carry_a_conversation(
    actions: ActionService, ticket_preview: dict[str, Any], propose: Propose, users: dict[str, User]
) -> None:
    """来源会话只来自待确认操作本身，模型的参数里带了也会被拒绝。"""
    with pytest.raises(ValidationError):
        await propose(users["alice"], "ticket", {"summary": "要人工", "conversation_id": 99}, ticket_preview)


# ---------- 各类型通用 ----------


async def test_unknown_kind_is_refused(propose: Propose, users: dict[str, User]) -> None:
    with pytest.raises(UnknownActionKindError):
        await propose(users["alice"], "refund", {}, {})


@pytest.mark.parametrize(
    ("kind", "result", "expected"),
    [
        (
            "return_cancellation",
            {"return_id": 7},
            "（撤回退货申请：退货申请 7（订单 5「SoundPods 降噪耳机」 × 1，退款 599.00 元）。状态：已撤回退货申请 7）",
        ),
        (
            "ticket",
            {"ticket_id": 3},
            "（转人工：订单 5，问题摘要：顾客要求换货。状态：已创建工单 #3，等待人工处理）",
        ),
    ],
)
async def test_description_for_the_model_depends_on_the_kind(
    actions: ActionService, kind: str, result: dict[str, Any], expected: str
) -> None:
    previews = {
        "return_cancellation": {
            "return_id": 7,
            "order_id": 5,
            "order_item_id": 9,
            "product_name": "SoundPods 降噪耳机",
            "quantity": 1,
            "refund_cents": 59900,
        },
        "ticket": {"order_id": 5, "summary": "顾客要求换货"},
    }
    action = PendingAction(kind=kind, preview=previews[kind], status="executed", result=result)

    assert actions.describe(action) == expected
