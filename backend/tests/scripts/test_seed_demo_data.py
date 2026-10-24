import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.orders import Orders
from app.models import Conversation, Message, Order, Product, User
from scripts.seed_demo_data import DEMO_ORDERS, DEMO_PRODUCTS, SeedResult, seed_demo_data

SessionFactory = async_sessionmaker[AsyncSession]


async def count(session_factory: SessionFactory, model: type) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(model))


async def test_running_twice_creates_demo_data_only_once(session_factory: SessionFactory) -> None:
    first = await seed_demo_data(session_factory, "alice")
    second = await seed_demo_data(session_factory, "alice")

    assert first == SeedResult(created_products=len(DEMO_PRODUCTS), created_orders=len(DEMO_ORDERS))
    assert second == SeedResult(created_products=0, created_orders=0)
    assert await count(session_factory, Product) == len(DEMO_PRODUCTS)
    assert await count(session_factory, Order) == len(DEMO_ORDERS)


async def test_seeded_orders_are_readable_through_business_layer(
    session_factory: SessionFactory, users: dict[str, User]
) -> None:
    await seed_demo_data(session_factory, "alice")

    orders = await Orders(session_factory).list_orders(users["alice"].id)

    assert len(orders) == len(DEMO_ORDERS)
    for order in orders:
        assert order.total_cents == sum(item.unit_price_cents * item.quantity for item in order.items)
        assert (order.status == "delivered") == (order.delivered_at is not None)


async def test_keeps_existing_chat_history(session_factory: SessionFactory, users: dict[str, User]) -> None:
    async with session_factory() as session:
        conversation = Conversation(user_id=users["alice"].id, title="之前的会话")
        session.add(conversation)
        await session.flush()
        session.add(Message(conversation_id=conversation.id, role="user", content="你好"))
        await session.commit()

    await seed_demo_data(session_factory, "alice")

    assert await count(session_factory, Conversation) == 1
    assert await count(session_factory, Message) == 1


async def test_unknown_user_changes_nothing(session_factory: SessionFactory) -> None:
    with pytest.raises(LookupError):
        await seed_demo_data(session_factory, "nobody")

    assert await count(session_factory, Product) == 0
