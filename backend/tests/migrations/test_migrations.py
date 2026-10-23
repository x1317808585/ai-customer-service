import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
TEST_DATABASE = "customer_service_test"


def run_alembic(*args: str) -> None:
    """在测试库上运行 alembic 命令（通过环境变量把库名换成测试库）。"""
    subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env={**os.environ, "POSTGRES_DB": TEST_DATABASE},
        check=True,
        capture_output=True,
    )


async def test_upgrade_keeps_existing_users_conversations_and_messages() -> None:
    engine = create_async_engine(Settings(postgres_db=TEST_DATABASE).database_url)
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))

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
    await engine.dispose()

    assert rows == [("old", None, "旧会话", "旧消息")]
