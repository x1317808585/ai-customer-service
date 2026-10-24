"""开发调试工具：以某个用户的身份调用 MCP 工具，看看 Agent 能拿到什么数据。要先启动 MCP Server。

它直接用本机 .env 里的 MCP_TOKEN_SECRET 签发令牌，不经过登录，只能在开发环境使用。
正式流程（9B）里，后端只给已经登录并通过校验的用户签发令牌。

用法（在 backend 目录下）：
    uv run python -m scripts.call_mcp_tool alice                                列出所有工具
    uv run python -m scripts.call_mcp_tool alice list_my_orders                 调用没有参数的工具
    uv run python -m scripts.call_mcp_tool alice search_products keyword=耳机   带参数（纯数字会转成整数）
"""

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, get_settings
from app.mcp_token import issue_mcp_token
from app.models import User


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="以某个用户的身份调用 MCP 工具")
    parser.add_argument("username")
    parser.add_argument("tool", nargs="?", help="工具名，不写就列出所有工具")
    parser.add_argument("arguments", nargs="*", help="工具参数，格式 name=value")
    return parser.parse_args()


def parse_tool_arguments(pairs: list[str]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator:
            sys.exit(f"参数格式应该是 name=value：{pair}")
        arguments[name] = int(value) if value.isdecimal() else value
    return arguments


async def find_user_id(settings: Settings, username: str) -> int:
    engine = create_async_engine(settings.database_url)
    async with async_sessionmaker(engine)() as session:
        user_id = await session.scalar(select(User.id).where(User.username == username))
    await engine.dispose()
    if user_id is None:
        sys.exit(f"用户 {username} 不存在")
    return user_id


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    token = issue_mcp_token(await find_user_id(settings, args.username), settings)
    try:
        await call_tool(settings.mcp_server_url, token, args.tool, parse_tool_arguments(args.arguments))
    except* httpx.ConnectError:
        sys.exit(f"连不上 MCP Server（{settings.mcp_server_url}），请先启动它")


async def call_tool(url: str, token: str, tool: str | None, arguments: dict[str, Any]) -> None:
    async with (
                # 连的是本机自己的服务，不走系统代理（否则请求会被代理截走，返回 502）
        httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        if tool is None:
            for listed in (await session.list_tools()).tools:
                print(f"{listed.name}：{listed.description}")
            return

        result = await session.call_tool(tool, arguments)
        if result.isError:
            print("工具返回错误：", "".join(b.text for b in result.content if isinstance(b, TextContent)))
        else:
            print(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
