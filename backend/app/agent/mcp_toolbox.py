from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.mcp_token import issue_mcp_token

# 允许交给模型的工具。MCP Server 以后增加了写操作工具，也不会自动交给模型，要在这里明确加上。
ALLOWED_TOOLS = frozenset(
    {
        "search_products",
        "get_product",
        "list_my_orders",
        "get_order",
        "list_my_returns",
        "list_my_tickets",
        "check_return_eligibility",
        "preview_return_request",
    }
)
MCP_HTTP_TIMEOUT_SECONDS = 15


class McpToolbox:
    """为某个用户打开一组 MCP 工具。连接在 async with 期间一直保持，退出时关闭。

    transport 只在测试里传：让请求直接进入内存里的 MCP 应用，不走网络。
    """

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    @asynccontextmanager
    async def open(self, user_id: int) -> AsyncIterator[list[BaseTool]]:
        token = issue_mcp_token(user_id, self._settings)
        async with (
            httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=MCP_HTTP_TIMEOUT_SECONDS,
                transport=self._transport,
                trust_env=False,  # 连的是自己的服务，不走系统代理
            ) as http_client,
            streamable_http_client(self._settings.mcp_server_url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await load_mcp_tools(session)
            yield [tool for tool in tools if tool.name in ALLOWED_TOOLS]
