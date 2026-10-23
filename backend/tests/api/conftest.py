import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.llm import OfflineChatModel
from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.models import Base, User

TEST_DATABASE = "customer_service_test"
ALLOWED_FRONTEND_ORIGIN = "http://localhost:3000"
TEST_JWT_SECRET = "test-jwt-secret-that-is-at-least-32-chars"
TEST_PASSWORD = "correct-password"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
OpenServer = Callable[[BaseChatModel], AbstractAsyncContextManager[AsyncClient]]


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


async def log_in(client: AsyncClient, username: str, password: str = TEST_PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture
def open_client(test_settings: Settings, users: dict[str, User]) -> OpenClient:
    """按需创建测试客户端：像浏览器一样带上前端的 Origin，并以指定用户真实登录。

    as_user=None 表示不登录；chat_model 可以换成测试用的替身模型。
    """

    @asynccontextmanager
    async def _open_client(
        as_user: str | None = "alice", chat_model: BaseChatModel | None = None
    ) -> AsyncIterator[AsyncClient]:
        app = create_app(test_settings, chat_model or OfflineChatModel(chunk_delay_seconds=0))
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Origin": ALLOWED_FRONTEND_ORIGIN},
            ) as client:
                if as_user is not None:
                    await log_in(client, as_user)
                yield client

    return _open_client


@pytest.fixture
async def client(open_client: OpenClient) -> AsyncIterator[AsyncClient]:
    """最常用的情况：以 alice 的身份登录、用离线模型。"""
    async with open_client() as client:
        yield client


@pytest.fixture
def open_server(test_settings: Settings, users: dict[str, User]) -> OpenServer:
    """启动一个真正的 uvicorn 服务器（端口随机），返回以 alice 身份登录的客户端。"""

    @asynccontextmanager
    async def _open_server(chat_model: BaseChatModel) -> AsyncIterator[AsyncClient]:
        app = create_app(test_settings, chat_model)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=1
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Origin": ALLOWED_FRONTEND_ORIGIN},
                trust_env=False,
            ) as client:
                await log_in(client, "alice")
                yield client
        finally:
            server.should_exit = True
            await server_task

    return _open_server
