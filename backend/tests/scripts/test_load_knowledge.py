"""导入脚本：默认只导入库里还没有的文档，加 --overwrite 才覆盖；格式不对时一篇都不导入。"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import DocumentFile, KnowledgeFormatError
from app.business.knowledge_documents import KnowledgeDocuments
from scripts.load_knowledge import import_files

FAQ_IN_REPOSITORY = DocumentFile("faq", "# 常见问题\n\n## 运费\n\n仓库里的内容。\n")
SHIPPING = DocumentFile("shipping", "# 配送说明\n\n## 运费\n\n所有订单包邮。\n")


@pytest.fixture
async def documents(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeDocuments:
    """库里已经有一篇管理员改过的 faq。"""
    documents = KnowledgeDocuments(session_factory)
    await documents.index(await documents.save(DocumentFile("faq", "# 常见问题\n\n## 运费\n\n管理员改过的内容。\n")))
    return documents


async def versions(documents: KnowledgeDocuments) -> dict[str, tuple[int, str]]:
    return {view.name: (view.version, view.status) for view in await documents.list_documents()}


async def test_existing_documents_are_skipped_unless_overwrite_is_given(documents: KnowledgeDocuments) -> None:
    assert await import_files(documents, [FAQ_IN_REPOSITORY, SHIPPING], overwrite=False) == (["shipping"], ["faq"])
    assert await versions(documents) == {"faq": (1, "ready"), "shipping": (1, "ready")}

    assert await import_files(documents, [FAQ_IN_REPOSITORY, SHIPPING], overwrite=True) == (["faq", "shipping"], [])
    assert await versions(documents) == {"faq": (2, "ready"), "shipping": (2, "ready")}


async def test_one_badly_formatted_file_imports_nothing(documents: KnowledgeDocuments) -> None:
    broken = DocumentFile("broken", "没有文档标题。\n")

    with pytest.raises(KnowledgeFormatError):
        await import_files(documents, [SHIPPING, broken], overwrite=False)

    assert await versions(documents) == {"faq": (1, "ready")}
