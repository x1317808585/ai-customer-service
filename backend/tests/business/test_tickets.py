from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.orders import OrderNotFoundError
from app.business.tickets import InvalidTicketError, Tickets
from app.models import Order, Ticket, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def tickets(session_factory: SessionFactory) -> Tickets:
    return Tickets(session_factory)


async def ticket_count(session_factory: SessionFactory) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Ticket))


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
