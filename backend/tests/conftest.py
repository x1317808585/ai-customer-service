from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.config import Settings
from app.models import Base, Order, OrderItem, OrderStatus, Product, User
from tests.constants import (
    ALLOWED_FRONTEND_ORIGIN,
    TEST_DATABASE,
    TEST_JWT_SECRET,
    TEST_MCP_TOKEN_SECRET,
    TEST_PASSWORD,
)

TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]


@pytest.fixture
def test_settings() -> Settings:
    """数据库账号沿用 .env，其余和测试结果有关的配置都在这里固定下来。"""
    return Settings(
        postgres_db=TEST_DATABASE,
        llm_provider="fake",
        frontend_origins=[ALLOWED_FRONTEND_ORIGIN],
        jwt_secret=TEST_JWT_SECRET,
        mcp_token_secret=TEST_MCP_TOKEN_SECRET,
    )


@pytest.fixture
async def users(test_settings: Settings) -> dict[str, User]:
    """每个测试开始前清空测试库并重建表，再创建 alice 和 bob 两个用户（密码相同）。"""
    if not test_settings.postgres_db.endswith("_test"):
        raise RuntimeError(f"拒绝清空非测试库：{test_settings.postgres_db}")

    engine = create_async_engine(test_settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        alice = User(username="alice", role="customer", password_hash=TEST_PASSWORD_HASH)
        bob = User(username="bob", role="customer", password_hash=TEST_PASSWORD_HASH)
        session.add_all([alice, bob])
        await session.commit()
    await engine.dispose()
    return {"alice": alice, "bob": bob}


@pytest.fixture
async def session_factory(
    test_settings: Settings, users: dict[str, User]
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """连接测试库的 Session 工厂。依赖 users，所以拿到它时表和用户都已经准备好了。"""
    engine = create_async_engine(test_settings.database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def add_product(session_factory: async_sessionmaker[AsyncSession]) -> AddProduct:
    """往测试库里加一个商品，返回它的 id。"""

    async def _add_product(
        name: str, price_cents: int = 1000, stock: int = 10, returnable: bool = True
    ) -> int:
        async with session_factory() as session:
            product = Product(
                name=name,
                description=f"{name}的商品说明",
                price_cents=price_cents,
                stock=stock,
                returnable=returnable,
            )
            session.add(product)
            await session.commit()
            return product.id

    return _add_product


@pytest.fixture
def add_order(session_factory: async_sessionmaker[AsyncSession]) -> AddOrder:
    """直接写入一个只有一项商品的订单（不扣库存），用来准备各种状态的订单。

    status 为 delivered 时必须同时给 delivered_at。返回的订单带着 items。
    """

    async def _add_order(
        user_id: int,
        product_id: int,
        quantity: int = 1,
        status: OrderStatus = "delivered",
        delivered_at: datetime | None = None,
    ) -> Order:
        async with session_factory() as session:
            product = await session.get_one(Product, product_id)
            item = OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price_cents=product.price_cents,
                quantity=quantity,
            )
            order = Order(
                user_id=user_id,
                status=status,
                total_cents=product.price_cents * quantity,
                delivered_at=delivered_at,
                items=[item],
            )
            session.add(order)
            await session.commit()
            return order

    return _add_order
