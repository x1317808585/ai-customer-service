from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

JWT_ALGORITHM = "HS256"

_password_hasher = PasswordHash.recommended()
_DUMMY_HASH = _password_hasher.hash("dummy-password-for-timing")


class InvalidTokenError(Exception):
    """令牌缺失、被篡改、已过期，或者内容不完整。"""


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


async def authenticate(session: AsyncSession, username: str, password: str) -> User | None:
    """用户名和密码都正确时返回用户，否则返回 None。

    用户不存在、没有设置密码时，也会做一次同样耗时的哈希校验，
    让“用户名不存在”和“密码错误”在响应时间上无法区分。
    """
    user = await session.scalar(select(User).where(User.username == username))
    stored_hash = user.password_hash if user and user.password_hash else _DUMMY_HASH
    password_matches = _password_hasher.verify(password, stored_hash)
    if user is None or user.password_hash is None or not password_matches:
        return None
    return user


def create_access_token(user_id: int, secret: str, lifetime: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + lifetime}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def read_user_id(token: str, secret: str) -> int:
    """校验令牌并取出用户 ID。任何不合格的令牌都抛出 InvalidTokenError。"""
    try:
        payload = jwt.decode(
            token, secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]}
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError) as error:
        raise InvalidTokenError from error
