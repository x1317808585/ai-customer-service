"""创建演示用户。登录功能完成前，所有请求都以这个用户的身份进行。可以重复运行。

用法（在 backend 目录下）：uv run python -m scripts.create_demo_user
"""

import asyncio

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.dependencies import DEMO_USERNAME
from app.config import get_settings
from app.models import User


async def main() -> None:
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.execute(
            insert(User)
            .values(username=DEMO_USERNAME, role="customer")
            .on_conflict_do_nothing(index_elements=[User.username])
        )
    await engine.dispose()
    print(f"演示用户 {DEMO_USERNAME} 已就绪")


if __name__ == "__main__":
    asyncio.run(main())
