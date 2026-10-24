from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.mcp_server.main import create_mcp_app
from app.mcp_token import issue_mcp_token
from app.models import User

OpenMcpHttp = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]
OpenMcpSession = Callable[..., AbstractAsyncContextManager[ClientSession]]


@pytest.fixture
def open_mcp_http(test_settings: Settings, users: dict[str, User]) -> OpenMcpHttp:
    """启动一个 MCP 应用（不占端口，直接在内存里调用），返回发往它的 HTTP 客户端。

    请求地址用 settings.mcp_server_url。token 为 None 表示不带令牌；
    settings 可以换成别的配置，比如故意连不上数据库。
    """

    @asynccontextmanager
    async def _open_mcp_http(
        token: str | None = None, settings: Settings | None = None
    ) -> AsyncIterator[httpx.AsyncClient]:
        app_settings = settings or test_settings
        app = create_mcp_app(app_settings)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), headers=headers) as client,
        ):
            yield client

    return _open_mcp_http


@pytest.fixture
def open_mcp_session(test_settings: Settings, users: dict[str, User], open_mcp_http: OpenMcpHttp) -> OpenMcpSession:
    """像后端的 Agent 一样，以某个用户的身份连上 MCP Server，返回已初始化的 MCP 会话。"""

    @asynccontextmanager
    async def _open_mcp_session(username: str, settings: Settings | None = None) -> AsyncIterator[ClientSession]:
        token = issue_mcp_token(users[username].id, test_settings)
        async with (
            open_mcp_http(token, settings) as http_client,
            streamable_http_client(test_settings.mcp_server_url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    return _open_mcp_session
