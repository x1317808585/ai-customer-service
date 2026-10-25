import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings
from app.models import Base
from tests.constants import TEST_DATABASE

BACKEND_DIR = Path(__file__).resolve().parents[2]


def run_alembic(*args: str, expect_failure: bool = False) -> str:
    """在测试库上运行 alembic 命令（通过环境变量把库名换成测试库），返回它的错误输出。

    子进程强制用 UTF-8 输出，Windows 上读到的中文错误信息才不会乱码。
    """
    finished = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "POSTGRES_DB": TEST_DATABASE, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
    )
    errors = finished.stderr.decode("utf-8", errors="replace")
    if expect_failure:
        assert finished.returncode != 0, "命令应该失败，却成功了"
    else:
        assert finished.returncode == 0, errors
    return errors


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """连接测试库，并清空整个库（包括 alembic 的版本记录），让迁移从头开始。"""
    engine = create_async_engine(Settings(postgres_db=TEST_DATABASE).database_url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    yield engine
    await engine.dispose()


async def current_version(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT version_num FROM alembic_version"))


async def test_upgrade_keeps_existing_users_conversations_and_messages(engine: AsyncEngine) -> None:
    run_alembic("upgrade", "+1")
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO users (username, role) VALUES ('old', 'customer')"))
        await connection.execute(text("INSERT INTO conversations (user_id, title) VALUES (1, '旧会话')"))
        await connection.execute(
            text("INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', '旧消息')")
        )

    run_alembic("upgrade", "head")
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT u.username, u.password_hash, c.title, m.content FROM users u "
                    "JOIN conversations c ON c.user_id = u.id JOIN messages m ON m.conversation_id = c.id"
                )
            )
        ).all()

    assert rows == [("old", None, "旧会话", "旧消息")]


async def test_migrated_schema_matches_the_models(engine: AsyncEngine) -> None:
    """迁移建出来的表结构要和模型一致：改了模型却忘了写迁移，这里会失败。

    注意：它发现不了 CHECK 约束内容的变化，那部分靠下面降级、升级的测试和代码评审。
    """
    run_alembic("upgrade", "head")

    async with engine.connect() as connection:
        differences = await connection.run_sync(
            lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
        )

    assert differences == []


async def test_downgrade_and_upgrade_again_when_no_new_data(engine: AsyncEngine) -> None:
    run_alembic("upgrade", "head")
    head = await current_version(engine)

    run_alembic("downgrade", "-1")
    run_alembic("upgrade", "head")

    assert await current_version(engine) == head


# 降级会破坏的两种数据：新类型的待确认操作、记录了来源会话的工单
NEW_DATA = {
    "new-kind-action": (
        "INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', '转人工'), (1, 'assistant', '卡片')",
        "INSERT INTO pending_actions (user_id, conversation_id, source_message_id, card_message_id, kind, "
        "arguments, preview, status, expires_at) VALUES (1, 1, 1, 2, 'ticket', '{}', '{}', 'pending', now())",
    ),
    "linked-ticket": (
        "INSERT INTO tickets (user_id, conversation_id, summary, status) VALUES (1, 1, '要人工', 'open')",
    ),
}


@pytest.mark.parametrize("data", NEW_DATA.values(), ids=NEW_DATA.keys())
async def test_downgrade_is_refused_instead_of_destroying_new_data(engine: AsyncEngine, data: tuple[str, ...]) -> None:
    run_alembic("upgrade", "head")
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO users (username, role) VALUES ('alice', 'customer')"))
        await connection.execute(text("INSERT INTO conversations (user_id, title) VALUES (1, '咨询')"))
        for statement in data:
            await connection.execute(text(statement))
    head = await current_version(engine)

    errors = run_alembic("downgrade", "-1", expect_failure=True)

    assert "不能降级" in errors
    assert await current_version(engine) == head
    async with engine.connect() as connection:
        actions = await connection.scalar(text("SELECT count(*) FROM pending_actions"))
        tickets = await connection.scalar(text("SELECT count(*) FROM tickets WHERE conversation_id IS NOT NULL"))
    assert actions + tickets == 1  # 数据原样保留
