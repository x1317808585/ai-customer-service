import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.business.returns import Returns
from app.config import Settings
from app.models import Base
from tests.constants import TEST_DATABASE

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 用到的迁移版本（文件名里的编号）
PENDING_ACTIONS = "8e8cacb502b2"  # 10A：待确认操作
ACTION_KINDS = "f7d42726c26d"  # 10B：撤回退货、转人工、工单来源会话
STAFF_RESOLUTION = "3c5a91d0b7e2"  # 11A：工单和退货的处理信息
KNOWLEDGE_CHUNKS = "13a4e21a91ea"  # 12A：知识库
CITATIONS = "8acbab16965e"  # 12B：回复引用的原文快照


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


async def execute(engine: AsyncEngine, *statements: str) -> None:
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


# ---------- 整体 ----------


async def test_upgrade_keeps_existing_users_conversations_and_messages(engine: AsyncEngine) -> None:
    run_alembic("upgrade", "+1")
    await execute(
        engine,
        "INSERT INTO users (username, role) VALUES ('old', 'customer')",
        "INSERT INTO conversations (user_id, title) VALUES (1, '旧会话')",
        "INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', '旧消息')",
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


# 比较时 Alembic 会把数据库里 NOT VALID 约束的标记原样传给 SQLAlchemy，后者提示不认识这个参数。不影响比较结果
@pytest.mark.filterwarnings("ignore:Can't validate argument 'dialect_options'")
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


async def test_recent_migrations_downgrade_and_upgrade_again_when_no_new_data(engine: AsyncEngine) -> None:
    run_alembic("upgrade", "head")
    head = await current_version(engine)

    run_alembic("downgrade", PENDING_ACTIONS)
    run_alembic("upgrade", "head")

    assert await current_version(engine) == head


# ---------- 10B：拒绝降级，不破坏新类型的数据 ----------

# 降级会破坏的两种数据：新类型的待确认操作、记录了来源会话的工单
ACTION_KINDS_DATA = {
    "new-kind-action": (
        "INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', '转人工'), (1, 'assistant', '卡片')",
        "INSERT INTO pending_actions (user_id, conversation_id, source_message_id, card_message_id, kind, "
        "arguments, preview, status, expires_at) VALUES (1, 1, 1, 2, 'ticket', '{}', '{}', 'pending', now())",
    ),
    "linked-ticket": (
        "INSERT INTO tickets (user_id, conversation_id, summary, status) VALUES (1, 1, '要人工', 'open')",
    ),
}


@pytest.mark.parametrize("data", ACTION_KINDS_DATA.values(), ids=ACTION_KINDS_DATA.keys())
async def test_action_kinds_downgrade_is_refused_instead_of_destroying_data(
    engine: AsyncEngine, data: tuple[str, ...]
) -> None:
    run_alembic("upgrade", ACTION_KINDS)
    await execute(
        engine,
        "INSERT INTO users (username, role) VALUES ('alice', 'customer')",
        "INSERT INTO conversations (user_id, title) VALUES (1, '咨询')",
        *data,
    )

    errors = run_alembic("downgrade", PENDING_ACTIONS, expect_failure=True)

    assert "不能降级" in errors
    assert await current_version(engine) == ACTION_KINDS
    async with engine.connect() as connection:
        actions = await connection.scalar(text("SELECT count(*) FROM pending_actions"))
        tickets = await connection.scalar(text("SELECT count(*) FROM tickets WHERE conversation_id IS NOT NULL"))
    assert actions + tickets == 1  # 数据原样保留


# ---------- 11A：历史记录不回填，新约束只管新数据 ----------

# 11A 之前就已经结束的退货申请：没有处理人，也没有原因
OLD_FINISHED_RETURN = (
    "INSERT INTO users (username, role) VALUES ('alice', 'customer'), ('admin', 'admin')",
    "INSERT INTO products (name, description, price_cents, stock) VALUES ('耳机', '降噪耳机', 59900, 5)",
    "INSERT INTO orders (user_id, status, total_cents, delivered_at) VALUES (1, 'delivered', 119800, now())",
    "INSERT INTO order_items (order_id, product_id, product_name, unit_price_cents, quantity) "
    "VALUES (1, 1, '耳机', 59900, 2)",
    "INSERT INTO return_requests (order_item_id, quantity, refund_cents, reason, status, resolved_at) "
    "VALUES (1, 1, 59900, '坏了', 'rejected', now())",
)


async def test_old_finished_returns_stay_readable_after_upgrade(engine: AsyncEngine) -> None:
    run_alembic("upgrade", ACTION_KINDS)
    await execute(engine, *OLD_FINISHED_RETURN)

    run_alembic("upgrade", "head")
    returns = Returns(async_sessionmaker(engine, expire_on_commit=False))
    [for_customer] = await returns.list_returns(1)
    for_staff = await returns.get_for_staff(for_customer.id)

    assert (for_customer.status, for_customer.resolution_note) == ("rejected", None)
    assert for_staff.resolved_by is None  # 历史记录没有处理人，不回填


@pytest.mark.parametrize(
    "statement",
    [
        # 新写入的已结束记录必须有处理人
        "INSERT INTO return_requests (order_item_id, quantity, refund_cents, reason, status, resolved_at) "
        "VALUES (1, 1, 59900, '坏了', 'completed', now())",
        # NOT VALID 只是添加约束时不扫描历史数据：以后修改历史记录，同样要满足约束
        "UPDATE return_requests SET reason = '改一下原因' WHERE id = 1",
    ],
    ids=["new-row", "modified-old-row"],
)
async def test_new_return_constraints_apply_to_new_and_modified_rows(engine: AsyncEngine, statement: str) -> None:
    run_alembic("upgrade", ACTION_KINDS)
    await execute(engine, *OLD_FINISHED_RETURN)
    run_alembic("upgrade", "head")

    with pytest.raises(IntegrityError):
        await execute(engine, statement)


# 降级会删掉的两种数据：已关闭工单的处理结果、退货申请的处理人和原因
STAFF_RESOLUTION_DATA = {
    "closed-ticket": (
        "INSERT INTO tickets (user_id, summary, status, resolution, resolved_by, resolved_at) "
        "VALUES (1, '物流慢', 'closed', '已联系物流', 2, now())",
    ),
    "resolved-return": (
        "INSERT INTO products (name, description, price_cents, stock) VALUES ('耳机', '降噪耳机', 59900, 5)",
        "INSERT INTO orders (user_id, status, total_cents, delivered_at) VALUES (1, 'delivered', 59900, now())",
        "INSERT INTO order_items (order_id, product_id, product_name, unit_price_cents, quantity) "
        "VALUES (1, 1, '耳机', 59900, 1)",
        "INSERT INTO return_requests (order_item_id, quantity, refund_cents, reason, status, resolved_by, "
        "resolution_note, resolved_at) VALUES (1, 1, 59900, '坏了', 'rejected', 2, '已使用', now())",
    ),
}


@pytest.mark.parametrize("data", STAFF_RESOLUTION_DATA.values(), ids=STAFF_RESOLUTION_DATA.keys())
async def test_staff_resolution_downgrade_is_refused_instead_of_destroying_data(
    engine: AsyncEngine, data: tuple[str, ...]
) -> None:
    run_alembic("upgrade", STAFF_RESOLUTION)
    await execute(engine, "INSERT INTO users (username, role) VALUES ('alice', 'customer'), ('admin', 'admin')", *data)

    errors = run_alembic("downgrade", ACTION_KINDS, expect_failure=True)

    assert "不能降级" in errors
    assert await current_version(engine) == STAFF_RESOLUTION


# ---------- 12B：有引用时拒绝降级 ----------


async def test_citations_downgrade_is_refused_instead_of_destroying_data(engine: AsyncEngine) -> None:
    run_alembic("upgrade", CITATIONS)
    await execute(
        engine,
        "INSERT INTO users (username, role) VALUES ('alice', 'customer')",
        "INSERT INTO conversations (user_id, title) VALUES (1, '退货')",
        "INSERT INTO messages (conversation_id, role, content, citations)"
        " VALUES (1, 'assistant', '回复', '[{\"source_id\": \"return-policy#退货期限\"}]')",
    )

    errors = run_alembic("downgrade", KNOWLEDGE_CHUNKS, expect_failure=True)

    assert "不能降级" in errors
    assert await current_version(engine) == CITATIONS
