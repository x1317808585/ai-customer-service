"""后端访问 MCP Server 时用的令牌：后端签发，MCP Server 校验。

令牌写明替哪个用户调用（sub）、谁签发的（iss）、给谁用的（aud），几分钟后就过期。
它和登录令牌用不同的密钥，两者不能互相冒充。
"""

from datetime import UTC, datetime, timedelta

import jwt

from app.auth import JWT_ALGORITHM, InvalidTokenError
from app.config import Settings

MCP_TOKEN_LIFETIME = timedelta(minutes=5)


def issue_mcp_token(user_id: int, settings: Settings, lifetime: timedelta = MCP_TOKEN_LIFETIME) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iss": settings.backend_url,
        "aud": settings.mcp_server_url,
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(payload, settings.mcp_token_secret.get_secret_value(), algorithm=JWT_ALGORITHM)


def read_mcp_user_id(token: str, settings: Settings) -> int:
    """校验令牌并取出用户 ID。任何不合格的令牌都抛出 InvalidTokenError。"""
    try:
        payload = jwt.decode(
            token,
            settings.mcp_token_secret.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
            audience=settings.mcp_server_url,
            issuer=settings.backend_url,
            options={"require": ["exp", "sub", "iss", "aud"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as error:
        raise InvalidTokenError from error
