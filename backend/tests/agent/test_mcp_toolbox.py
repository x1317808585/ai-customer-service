from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import SecretStr

from app.agent.mcp_toolbox import ALLOWED_TOOLS, McpToolbox
from app.config import Settings
from app.mcp_server.main import create_mcp_app
from app.models import User
from tests.conftest import InMemoryMcpToolbox


@asynccontextmanager
async def server_with_an_extra_write_tool() -> AsyncIterator[httpx.ASGITransport]:
    """一个不做鉴权的小 MCP Server：除了一个查询工具，还多了一个不在白名单里的写操作工具。"""
    mcp = FastMCP("extra", stateless_http=True, json_response=True)

    @mcp.tool()
    async def list_my_orders() -> list[int]:
        return []

    @mcp.tool()
    async def cancel_all_orders() -> str:
        return "全部取消了"

    app = mcp.streamable_http_app()
    async with app.router.lifespan_context(app):
        yield httpx.ASGITransport(app=app)


async def test_gives_exactly_the_six_query_tools(mcp_toolbox: InMemoryMcpToolbox, users: dict[str, User]) -> None:
    async with mcp_toolbox.open(users["alice"].id) as tools:
        names = {tool.name for tool in tools}

    assert names == ALLOWED_TOOLS


async def test_tools_outside_the_allow_list_are_never_handed_out(test_settings: Settings) -> None:
    async with (
        server_with_an_extra_write_tool() as transport,
        McpToolbox(test_settings, transport).open(1) as tools,
    ):
        names = {tool.name for tool in tools}

    assert names == {"list_my_orders"}


async def test_rejected_token_means_no_tools(test_settings: Settings, users: dict[str, User]) -> None:
    other_secret = test_settings.model_copy(update={"mcp_token_secret": SecretStr("a-different-secret-of-32-chars-x")})
    mcp_app = create_mcp_app(test_settings)

    async with mcp_app.router.lifespan_context(mcp_app):
        toolbox = McpToolbox(other_secret, httpx.ASGITransport(app=mcp_app))
        with pytest.raises(Exception) as error:
            async with toolbox.open(users["alice"].id):
                pass

    assert "401" in repr(error.value)
