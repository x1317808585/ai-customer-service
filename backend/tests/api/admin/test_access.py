import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.main import create_app
from app.models import User

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
SessionFactory = async_sessionmaker[AsyncSession]


def admin_routes(settings: Settings) -> list[tuple[str, str]]:
    """应用里所有 /api/admin 开头的接口（方法, 路径），路径参数都填成 1。以后新增的管理接口会自动包含进来。

    从 OpenAPI 文档里读取：它列出了应用对外提供的全部接口。
    """
    paths = create_app(settings).openapi()["paths"]
    return [
        (method.upper(), re.sub(r"\{[^}]+\}", "1", path))
        for path, operations in paths.items()
        if path.startswith("/api/admin")
        for method in operations
    ]


async def statuses(client: AsyncClient, routes: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    return {(method, path): (await client.request(method, path, json={})).status_code for method, path in routes}


async def test_every_admin_route_requires_an_admin(open_client: OpenClient, test_settings: Settings) -> None:
    routes = admin_routes(test_settings)
    assert len(routes) >= 7  # 防止路由没注册上时测试空跑通过

    async with open_client(as_user=None) as anonymous:
        assert set((await statuses(anonymous, routes)).values()) == {401}
    async with open_client(as_user="alice") as customer:
        assert set((await statuses(customer, routes)).values()) == {403}
    async with open_client(as_user="admin") as admin:
        assert not {401, 403} & set((await statuses(admin, routes)).values())


async def test_revoking_admin_role_takes_effect_on_the_next_request(
    open_client: OpenClient, users: dict[str, User], session_factory: SessionFactory
) -> None:
    async with open_client(as_user="admin") as admin:
        before = await admin.get("/api/admin/tickets")
        async with session_factory() as session:
            await session.execute(update(User).where(User.id == users["admin"].id).values(role="customer"))
            await session.commit()
        after = await admin.get("/api/admin/tickets")

    assert (before.status_code, after.status_code) == (200, 403)


async def test_admin_post_from_another_site_is_rejected(open_client: OpenClient) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(
            "/api/admin/tickets/1/close", json={"resolution": "已处理"}, headers={"Origin": "http://evil.example.com"}
        )

    assert response.status_code == 403
