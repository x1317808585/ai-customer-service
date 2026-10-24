from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from pydantic import SecretStr

from app.auth import JWT_ALGORITHM, create_access_token
from app.config import Settings
from app.mcp_token import issue_mcp_token
from app.models import User
from tests.mcp_server.conftest import OpenMcpHttp

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def bad_tokens(settings: Settings, user_id: int) -> dict[str, str]:
    """各种不该被接受的令牌。"""

    def signed(payload: dict[str, Any]) -> str:
        """用正确的密钥签名，但内容由测试决定，用来构造缺字段或字段不对的令牌。"""
        return jwt.encode(payload, settings.mcp_token_secret.get_secret_value(), algorithm=JWT_ALGORITHM)

    complete = {
        "sub": str(user_id),
        "iss": settings.backend_url,
        "aud": settings.mcp_server_url,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }

    def without(claim: str) -> dict[str, Any]:
        return {name: value for name, value in complete.items() if name != claim}

    return {
        "wrong-secret": issue_mcp_token(
            user_id, settings.model_copy(update={"mcp_token_secret": SecretStr("another-secret-that-is-32-chars-long")})
        ),
        "expired": issue_mcp_token(user_id, settings, lifetime=timedelta(seconds=-1)),
        "for-another-server": issue_mcp_token(
            user_id, settings.model_copy(update={"mcp_server_url": "http://elsewhere:8001/mcp"})
        ),
        "from-another-issuer": issue_mcp_token(
            user_id, settings.model_copy(update={"backend_url": "http://elsewhere:8000"})
        ),
        "login-cookie-token": create_access_token(
            user_id, settings.jwt_secret.get_secret_value(), timedelta(minutes=5)
        ),
        "missing-exp": signed(without("exp")),
        "missing-sub": signed(without("sub")),
        "missing-iss": signed(without("iss")),
        "missing-aud": signed(without("aud")),
        "non-numeric-sub": signed(complete | {"sub": "alice"}),
        "garbage": "not-a-jwt",
    }


BAD_TOKEN_KINDS = [
    "wrong-secret",
    "expired",
    "for-another-server",
    "from-another-issuer",
    "login-cookie-token",
    "missing-exp",
    "missing-sub",
    "missing-iss",
    "missing-aud",
    "non-numeric-sub",
    "garbage",
]


async def test_request_without_token_is_unauthorized(open_mcp_http: OpenMcpHttp, test_settings: Settings) -> None:
    async with open_mcp_http() as client:
        response = await client.post(test_settings.mcp_server_url, json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]


@pytest.mark.parametrize("kind", BAD_TOKEN_KINDS)
async def test_invalid_tokens_are_unauthorized(
    open_mcp_http: OpenMcpHttp, test_settings: Settings, users: dict[str, User], kind: str
) -> None:
    token = bad_tokens(test_settings, users["alice"].id)[kind]

    async with open_mcp_http(token) as client:
        response = await client.post(test_settings.mcp_server_url, json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 401


async def test_valid_token_is_accepted(
    open_mcp_http: OpenMcpHttp, test_settings: Settings, users: dict[str, User]
) -> None:
    async with open_mcp_http(issue_mcp_token(users["alice"].id, test_settings)) as client:
        response = await client.post(test_settings.mcp_server_url, json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 200
