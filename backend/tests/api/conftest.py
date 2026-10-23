from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.llm import OfflineChatModel
from app.api.dependencies import get_current_user
from app.config import Settings
from app.main import create_app
from app.models import Base, User

TEST_DATABASE = "customer_service_test"
ALLOWED_FRONTEND_ORIGIN = "http://localhost:3000"

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def test_settings() -> Settings:
    """数据库账号沿用 .env，库名换成测试库；模型和允许跨域的前端地址都固定下来。"""
    return Settings(
        postgres_db=TEST_DATABASE,
        llm_provider="fake",
        frontend_origins=[ALLOWED_FRONTEND_ORIGIN],
    )


@pytest.fixture
async def users(test_settings: Settings) -> dict[str, User]:
    """每个测试开始前清空测试库并重建表，再创建 alice 和 bob 两个用户。"""
    if not test_settings.postgres_db.endswith("_test"):
        raise RuntimeError(f"拒绝清空非测试库：{test_settings.postgres_db}")

    engine = create_async_engine(test_settings.database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        alice = User(username="alice", role="customer")
        bob = User(username="bob", role="customer")
        session.add_all([alice, bob])
        await session.commit()
    await engine.dispose()
    return {"alice": alice, "bob": bob}


@pytest.fixture
def open_client(test_settings: Settings, users: dict[str, User]) -> OpenClient:
    """按需创建测试客户端：可以指定以哪个用户的身份请求、用哪个模型回复。"""

    @asynccontextmanager
    async def _open_client(
        as_user: str = "alice", chat_model: BaseChatModel | None = None
    ) -> AsyncIterator[AsyncClient]:
        app = create_app(test_settings, chat_model or OfflineChatModel(chunk_delay_seconds=0))
        app.dependency_overrides[get_current_user] = lambda: users[as_user]
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client

    return _open_client


@pytest.fixture
async def client(open_client: OpenClient) -> AsyncIterator[AsyncClient]:
    """最常用的情况：以 alice 的身份、用离线模型。"""
    async with open_client() as client:
        yield client
