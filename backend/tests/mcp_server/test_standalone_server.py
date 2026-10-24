import asyncio
import os
import socket
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.config import Settings
from app.mcp_token import issue_mcp_token
from app.models import Order, User

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]

BACKEND_DIR = Path(__file__).resolve().parents[2]
STARTUP_TIMEOUT_SECONDS = 30


def free_port() -> int:
    """让系统分配一个当前没人用的端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def wait_until_listening(process: asyncio.subprocess.Process, port: int) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.returncode is not None:
            raise RuntimeError(f"MCP Server 进程启动失败，退出码 {process.returncode}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.2)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(f"MCP Server {STARTUP_TIMEOUT_SECONDS} 秒内没有开始监听端口 {port}")


async def test_standalone_server_answers_over_real_http(
    test_settings: Settings, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    """用和手动启动完全相同的命令启动独立进程，再通过真实网络连接它。"""
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, status="shipped")
    port = free_port()
    settings = test_settings.model_copy(update={"mcp_server_url": f"http://localhost:{port}/mcp"})
    environment = {
        **os.environ,
        "POSTGRES_DB": settings.postgres_db,
        "JWT_SECRET": settings.jwt_secret.get_secret_value(),
        "MCP_TOKEN_SECRET": settings.mcp_token_secret.get_secret_value(),
        "MCP_SERVER_URL": settings.mcp_server_url,
    }
    command = [sys.executable, "-m", "uvicorn", "app.mcp_server.main:create_mcp_app", "--factory"]
    process = await asyncio.create_subprocess_exec(
        *command, "--port", str(port), "--log-level", "warning", cwd=BACKEND_DIR, env=environment
    )
    try:
        await wait_until_listening(process, port)
        token = issue_mcp_token(users["alice"].id, settings)
        async with (
            httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, trust_env=False) as http_client,
            streamable_http_client(settings.mcp_server_url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("list_my_orders", {})
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)

    assert not result.isError
    assert result.structuredContent is not None
    assert [listed["id"] for listed in result.structuredContent["result"]] == [order.id]
