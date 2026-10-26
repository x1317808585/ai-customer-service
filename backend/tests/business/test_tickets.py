import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.orders import OrderNotFoundError
from app.business.tickets import (
    InvalidTicketError,
    SourceConversationNotFoundError,
    TicketAlreadyClosedError,
    TicketNotFoundError,
    TicketPreview,
    Tickets,
)
from app.models import Conversation, Order, Ticket, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


@pytest.fixture
def tickets(session_factory: SessionFactory) -> Tickets:
    return Tickets(session_factory, clock=lambda: NOW)


async def ticket_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Ticket))


async def create_conversation(session_factory: SessionFactory, user: User) -> int:
    async with session_factory() as session, session.begin():
        conversation = Conversation(user_id=user.id, title="咨询")
        session.add(conversation)
        await session.flush()
        return conversation.id


async def test_open_ticket_about_own_order(
    tickets: Tickets, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, status="shipped")

    ticket = await tickets.open_ticket(users["alice"].id, "  物流三天没更新，顾客要求人工跟进  ", order_id=order.id)

    assert (ticket.status, ticket.order_id, ticket.summary) == ("open", order.id, "物流三天没更新，顾客要求人工跟进")


async def test_open_ticket_without_order(tickets: Tickets, users: dict[str, User]) -> None:
    ticket = await tickets.open_ticket(users["alice"].id, "想咨询企业采购")

    assert ticket.order_id is None


async def test_cannot_open_ticket_about_someone_elses_order(
    tickets: Tickets,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, status="shipped")

    with pytest.raises(OrderNotFoundError):
        await tickets.open_ticket(users["bob"].id, "帮我查一下这个订单", order_id=order.id)
    assert await ticket_count(session_factory) == 0


@pytest.mark.parametrize("summary", ["   ", "长" * 1001], ids=["blank", "too-long"])
async def test_summary_must_be_present_and_short(tickets: Tickets, users: dict[str, User], summary: str) -> None:
    with pytest.raises(InvalidTicketError):
        await tickets.open_ticket(users["alice"].id, summary)


async def test_users_only_see_their_own_tickets_newest_first(tickets: Tickets, users: dict[str, User]) -> None:
    first = await tickets.open_ticket(users["alice"].id, "第一个问题")
    second = await tickets.open_ticket(users["alice"].id, "第二个问题")

    assert [ticket.id for ticket in await tickets.list_tickets(users["alice"].id)] == [second.id, first.id]
    assert await tickets.list_tickets(users["bob"].id) == []


# ---------- 预览，以及在调用方的事务里创建（转人工卡片用） ----------


async def test_preview_checks_the_ticket_but_writes_nothing(
    tickets: Tickets,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, status="shipped")

    preview = await tickets.preview_ticket(users["alice"].id, "  顾客反馈物流三天没更新  ", order_id=order.id)

    assert preview == TicketPreview(order_id=order.id, summary="顾客反馈物流三天没更新")
    with pytest.raises(OrderNotFoundError):
        await tickets.preview_ticket(users["bob"].id, "帮我查一下这个订单", order_id=order.id)
    with pytest.raises(InvalidTicketError):
        await tickets.preview_ticket(users["alice"].id, "   ")
    assert await ticket_count(session_factory) == 0


async def test_ticket_remembers_the_conversation_it_came_from(
    tickets: Tickets, users: dict[str, User], session_factory: SessionFactory
) -> None:
    conversation_id = await create_conversation(session_factory, users["alice"])

    async with session_factory() as session, session.begin():
        created = await tickets.open_ticket_in(
            session, users["alice"].id, "顾客要求人工处理", conversation_id=conversation_id
        )

    async with session_factory() as session:
        saved = await session.get_one(Ticket, created.id)
    assert (saved.conversation_id, saved.summary, saved.status) == (conversation_id, "顾客要求人工处理", "open")


async def test_ticket_cannot_point_at_someone_elses_conversation(
    tickets: Tickets, users: dict[str, User], session_factory: SessionFactory
) -> None:
    bobs_conversation = await create_conversation(session_factory, users["bob"])

    async with session_factory() as session, session.begin():
        with pytest.raises(SourceConversationNotFoundError):
            await tickets.open_ticket_in(
                session, users["alice"].id, "顾客要求人工处理", conversation_id=bobs_conversation
            )

    assert await ticket_count(session_factory) == 0


async def test_ticket_in_callers_transaction_is_undone_when_caller_rolls_back(
    tickets: Tickets, users: dict[str, User], session_factory: SessionFactory
) -> None:
    async with session_factory() as session:
        await session.begin()
        await tickets.open_ticket_in(session, users["alice"].id, "顾客要求人工处理")
        await session.rollback()

    assert await ticket_count(session_factory) == 0


# ---------- 管理员处理工单 ----------


async def test_closing_records_resolution_staff_and_time(tickets: Tickets, users: dict[str, User]) -> None:
    opened = await tickets.open_ticket(users["alice"].id, "物流三天没更新")

    closed = await tickets.close_ticket(users["admin"].id, opened.id, "  已联系物流补发  ")

    assert (closed.status, closed.resolution, closed.resolved_by, closed.resolved_at) == (
        "closed",
        "已联系物流补发",
        "admin",
        NOW,
    )
    [seen_by_customer] = await tickets.list_tickets(users["alice"].id)
    assert (seen_by_customer.status, seen_by_customer.resolution) == ("closed", "已联系物流补发")
    assert "resolved_by" not in seen_by_customer.model_dump()  # 顾客看不到处理人


@pytest.mark.parametrize("resolution", ["   ", "长" * 2001], ids=["blank", "too-long"])
async def test_closing_requires_a_short_resolution(tickets: Tickets, users: dict[str, User], resolution: str) -> None:
    opened = await tickets.open_ticket(users["alice"].id, "物流三天没更新")

    with pytest.raises(InvalidTicketError):
        await tickets.close_ticket(users["admin"].id, opened.id, resolution)

    assert (await tickets.get_for_staff(opened.id)).status == "open"


async def test_closed_ticket_cannot_be_closed_again(tickets: Tickets, users: dict[str, User]) -> None:
    opened = await tickets.open_ticket(users["alice"].id, "物流三天没更新")
    await tickets.close_ticket(users["admin"].id, opened.id, "已联系物流补发")

    with pytest.raises(TicketAlreadyClosedError):
        await tickets.close_ticket(users["admin"].id, opened.id, "再处理一次")

    assert (await tickets.get_for_staff(opened.id)).resolution == "已联系物流补发"


async def test_two_staff_closing_at_the_same_time_only_one_wins(
    tickets: Tickets, users: dict[str, User], session_factory: SessionFactory
) -> None:
    opened = await tickets.open_ticket(users["alice"].id, "物流三天没更新")

    async with session_factory() as blocker, blocker.begin():
        await blocker.execute(select(Ticket).where(Ticket.id == opened.id).with_for_update())
        attempts = [
            asyncio.create_task(tickets.close_ticket(users["admin"].id, opened.id, f"处理结果 {n}")) for n in range(3)
        ]
        await asyncio.sleep(0.5)
    results = await asyncio.gather(*attempts, return_exceptions=True)

    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(failures) == 2 and all(isinstance(failure, TicketAlreadyClosedError) for failure in failures)


async def test_staff_see_tickets_of_every_customer_by_status(
    tickets: Tickets, users: dict[str, User], session_factory: SessionFactory
) -> None:
    conversation_id = await create_conversation(session_factory, users["alice"])
    async with session_factory() as session, session.begin():
        from_chat = await tickets.open_ticket_in(session, users["alice"].id, "要人工", conversation_id=conversation_id)
    from_bob = await tickets.open_ticket(users["bob"].id, "想咨询企业采购")
    await tickets.close_ticket(users["admin"].id, from_bob.id, "已电话联系")

    open_tickets = await tickets.list_for_staff("open")
    closed_tickets = await tickets.list_for_staff("closed")

    assert [(t.id, t.customer, t.conversation_id) for t in open_tickets] == [(from_chat.id, "alice", conversation_id)]
    assert [(t.id, t.customer, t.resolved_by) for t in closed_tickets] == [(from_bob.id, "bob", "admin")]


async def test_unknown_ticket_is_not_found(tickets: Tickets, users: dict[str, User]) -> None:
    with pytest.raises(TicketNotFoundError):
        await tickets.get_for_staff(999)
    with pytest.raises(TicketNotFoundError):
        await tickets.close_ticket(users["admin"].id, 999, "已处理")
