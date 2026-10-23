from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth import create_access_token
from app.config import Settings
from app.models import User

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]

PASSWORD = "correct-password"
NOT_LOGGED_IN = {"detail": "未登录或登录已过期"}


async def login(client: AsyncClient, username: str, password: str):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


# ---------- 登录 ----------


async def test_login_sets_http_only_cookie_and_returns_user(open_client: OpenClient) -> None:
    async with open_client(as_user=None) as client:
        response = await login(client, "alice", PASSWORD)

    assert response.status_code == 200
    assert response.json() == {"id": 1, "username": "alice", "role": "customer"}
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("access_token=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie


async def test_wrong_password_and_unknown_user_get_the_same_answer(open_client: OpenClient) -> None:
    async with open_client(as_user=None) as client:
        wrong_password = await login(client, "alice", "wrong-password")
        unknown_user = await login(client, "nobody", PASSWORD)

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == {"detail": "用户名或密码错误"}
    assert "set-cookie" not in wrong_password.headers


async def test_user_without_password_cannot_log_in_until_one_is_set(
    open_client: OpenClient, test_settings: Settings
) -> None:
    engine = create_async_engine(test_settings.database_url)
    async with engine.begin() as connection:
        await connection.execute(update(User).where(User.username == "bob").values(password_hash=None))
    await engine.dispose()

    async with open_client(as_user=None) as client:
        response = await login(client, "bob", PASSWORD)

    assert response.status_code == 401


async def test_password_is_stored_as_argon2_hash(
    users: dict[str, User], test_settings: Settings
) -> None:
    engine = create_async_engine(test_settings.database_url)
    async with engine.connect() as connection:
        stored = await connection.scalar(select(User.password_hash).where(User.username == "alice"))
    await engine.dispose()

    assert stored is not None
    assert stored.startswith("$argon2")
    assert PASSWORD not in stored


# ---------- 当前用户 ----------


async def test_me_returns_logged_in_user(client: AsyncClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.json() == {"id": 1, "username": "alice", "role": "customer"}


async def test_logout_clears_cookie_and_later_requests_are_unauthorized(client: AsyncClient) -> None:
    logout = await client.post("/api/auth/logout")
    after = await client.get("/api/conversations")

    assert logout.status_code == 204
    assert "max-age=0" in logout.headers["set-cookie"].lower()
    assert after.status_code == 401


# ---------- 令牌校验 ----------


def _token(claims: dict, secret: str) -> str:
    return jwt.encode(claims, secret, algorithm="HS256")


def _in(minutes: int) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


@pytest.mark.parametrize(
    "make_token",
    [
        lambda secret: None,
        lambda secret: create_access_token(1, secret, timedelta(minutes=5))[:-2] + "xx",
        lambda secret: create_access_token(1, secret, timedelta(seconds=-1)),
        lambda secret: _token({"sub": "1"}, secret),
        lambda secret: _token({"sub": "1", "exp": _in(5)}, "another-secret-that-is-32-characters-long"),
        lambda secret: _token({"sub": "999", "exp": _in(5)}, secret),
        lambda secret: jwt.encode({"sub": "1", "exp": _in(5)}, None, algorithm="none"),
    ],
    ids=["no-cookie", "tampered", "expired", "no-exp", "wrong-secret", "deleted-user", "alg-none"],
)
async def test_invalid_tokens_are_rejected(
    open_client: OpenClient, test_settings: Settings, make_token: Callable[[str], str | None]
) -> None:
    token = make_token(test_settings.jwt_secret.get_secret_value())
    async with open_client(as_user=None) as client:
        if token is not None:
            client.cookies.set("access_token", token)
        response = await client.get("/api/conversations")

    assert response.status_code == 401
    assert response.json() == NOT_LOGGED_IN


# ---------- 用户之间的数据隔离（真实登录） ----------


async def test_other_users_conversation_is_invisible_unreadable_and_unwritable(
    open_client: OpenClient,
) -> None:
    async with open_client(as_user="alice") as alice, open_client(as_user="bob") as bob:
        conversation_id = (await alice.post("/api/conversations")).json()["id"]

        bob_list = (await bob.get("/api/conversations")).json()
        bob_history = await bob.get(f"/api/conversations/{conversation_id}/messages")
        bob_send = await bob.post(
            f"/api/conversations/{conversation_id}/messages", json={"content": "你好"}
        )
        alice_history = (await alice.get(f"/api/conversations/{conversation_id}/messages")).json()

    assert bob_list == []
    assert bob_history.status_code == 404
    assert bob_send.status_code == 404
    assert alice_history == []
