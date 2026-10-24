import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import pytest
import uvicorn
from httpx import ASGITransport, AsyncClient
from langchain_core.language_models import BaseChatModel

from app.agent.llm import OfflineChatModel
from app.agent.reply_agent import Toolbox
from app.config import Settings
from app.main import create_app
from app.models import User
from tests.conftest import InMemoryMcpToolbox
from tests.constants import ALLOWED_FRONTEND_ORIGIN, TEST_PASSWORD

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
OpenServer = Callable[..., AbstractAsyncContextManager[AsyncClient]]


async def log_in(client: AsyncClient, username: str, password: str = TEST_PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


@pytest.fixture
def open_client(test_settings: Settings, users: dict[str, User], mcp_toolbox: InMemoryMcpToolbox) -> OpenClient:
    """按需创建测试客户端：像浏览器一样带上前端的 Origin，并以指定用户真实登录。

    as_user=None 表示不登录；chat_model 可以换成测试用的替身模型；
    toolbox 默认连接内存里真实的 MCP Server，也可以换成替身。
    """

    @asynccontextmanager
    async def _open_client(
        as_user: str | None = "alice",
        chat_model: BaseChatModel | None = None,
        toolbox: Toolbox | None = None,
    ) -> AsyncIterator[AsyncClient]:
        model = chat_model or OfflineChatModel(chunk_delay_seconds=0)
        app = create_app(test_settings, model, toolbox or mcp_toolbox)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", headers={"Origin": ALLOWED_FRONTEND_ORIGIN}
            ) as client,
        ):
            if as_user is not None:
                await log_in(client, as_user)
            yield client

    return _open_client


@pytest.fixture
async def client(open_client: OpenClient) -> AsyncIterator[AsyncClient]:
    """最常用的情况：以 alice 的身份登录、用离线模型、连接真实 MCP Server。"""
    async with open_client() as client:
        yield client


@pytest.fixture
def open_server(test_settings: Settings, users: dict[str, User], mcp_toolbox: InMemoryMcpToolbox) -> OpenServer:
    """启动一个真正的 uvicorn 服务器（端口随机），返回以 alice 身份登录的客户端。"""

    @asynccontextmanager
    async def _open_server(chat_model: BaseChatModel, toolbox: Toolbox | None = None) -> AsyncIterator[AsyncClient]:
        app = create_app(test_settings, chat_model, toolbox or mcp_toolbox)
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", timeout_graceful_shutdown=1)
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with AsyncClient(
                base_url=f"http://127.0.0.1:{port}", headers={"Origin": ALLOWED_FRONTEND_ORIGIN}, trust_env=False
            ) as client:
                await log_in(client, "alice")
                yield client
        finally:
            server.should_exit = True
            await server_task

    return _open_server
