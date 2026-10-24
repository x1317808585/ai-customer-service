from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.applications import Starlette
from starlette.routing import Mount

from app.auth import InvalidTokenError
from app.config import Settings, get_settings
from app.mcp_server.tools import register_read_tools
from app.mcp_token import read_mcp_user_id

SERVER_NAME = "customer-service-business"
BACKEND_CLIENT_ID = "customer-service-backend"


class UserTokenVerifier:
    """校验后端签发的令牌。合格就告诉 SDK 是哪个用户（subject），不合格返回 None，SDK 会回 401。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            user_id = read_mcp_user_id(token, self._settings)
        except InvalidTokenError:
            return None
        return AccessToken(token=token, client_id=BACKEND_CLIENT_ID, scopes=[], subject=str(user_id))


def create_mcp_app(settings: Settings | None = None) -> Starlette:
    """创建 MCP Server 的 ASGI 应用。settings 不传时读取 .env。

    这里只创建对象，不连接数据库（引擎第一次查询时才建立连接）。
    启动命令：uv run uvicorn app.mcp_server.main:create_mcp_app --factory --port 8001
    """
    app_settings = settings or get_settings()
    engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)

    mcp = FastMCP(
        SERVER_NAME,
        instructions="电商客服的业务数据工具。所有查询都只针对当前顾客自己的数据。",
        token_verifier=UserTokenVerifier(app_settings),
        auth=AuthSettings(
            issuer_url=app_settings.backend_url,
            resource_server_url=app_settings.mcp_server_url,
            validate_token_resource=False,  # 令牌的 aud 已经由 UserTokenVerifier 校验
        ),
        stateless_http=True,
        json_response=True,
    )
    register_read_tools(mcp, async_sessionmaker(engine, expire_on_commit=False))
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            await engine.dispose()

    return Starlette(routes=[Mount("/", app=mcp_app)], lifespan=lifespan)
