from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import hash_password
from app.config import Settings
from app.models import Base, User
from tests.constants import ALLOWED_FRONTEND_ORIGIN, TEST_DATABASE, TEST_JWT_SECRET, TEST_PASSWORD

TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


@pytest.fixture
def test_settings() -> Settings:
    """数据库账号沿用 .env，其余和测试结果有关的配置都在这里固定下来。"""
    return Settings(
        postgres_db=TEST_DATABASE,
        llm_provider="fake",
        frontend_origins=[ALLOWED_FRONTEND_ORIGIN],
        jwt_secret=TEST_JWT_SECRET,
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
