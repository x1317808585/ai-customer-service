"""15A：知识文档的迁移。数据库是文档的来源，库里有文档时拒绝降级。"""

from sqlalchemy.ext.asyncio import AsyncEngine

from tests.migrations.test_migrations import current_version, engine, execute, run_alembic  # noqa: F401（engine 是夹具）

EMBEDDINGS = "c4e8a1f2b7d3"  # 14A：知识库向量化
KNOWLEDGE_DOCUMENTS = "d7f3b2a9c5e1"  # 15A：知识文档


async def test_knowledge_documents_downgrade_is_refused_instead_of_destroying_data(engine: AsyncEngine) -> None:  # noqa: F811
    run_alembic("upgrade", KNOWLEDGE_DOCUMENTS)
    await execute(
        engine,
        "INSERT INTO knowledge_documents (name, title, markdown, version, index_job, status)"
        " VALUES ('faq', '常见问题', '# 常见问题', 1, 1, 'pending')",
    )

    errors = run_alembic("downgrade", EMBEDDINGS, expect_failure=True)

    assert "不能降级" in errors
    assert await current_version(engine) == KNOWLEDGE_DOCUMENTS
