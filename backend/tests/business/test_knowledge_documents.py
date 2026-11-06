"""知识文档的保存和建索引：版本和任务号、过期任务、状态、启用和停用。"""

import asyncio
import threading
from collections.abc import Sequence

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import DocumentFile, KnowledgeBase, KnowledgeFormatError, Supported
from app.business.knowledge_documents import (
    INTERRUPTED_MESSAGE,
    SYSTEM_ERROR_MESSAGE,
    DocumentNotFoundError,
    KnowledgeDocuments,
)
from app.models import EMBEDDING_DIMENSION, KnowledgeChunk, KnowledgeDocument
from tests.business.fake_embedder import FakeEmbedder

EARBUDS = DocumentFile("soundpods", "# SoundPods 降噪耳机说明书\n\n## 保修\n\n整机保修 1 年。\n")
QUESTION = "K8 是什么轴？"


def keyboard(switch: str) -> DocumentFile:
    return DocumentFile("keyboard-k8", f"# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔{switch}。\n")


class PausingEmbedder(FakeEmbedder):
    """第一次编码时停下来，等测试放行：用来在建索引的半路插入别的操作。"""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self._paused = False

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._paused:
            self._paused = True
            self.started.set()
            self.release.wait(timeout=10)
        return super().embed(texts)


class BrokenEmbedder(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("模型出错了")


@pytest.fixture
def documents(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeDocuments:
    return KnowledgeDocuments(session_factory)


@pytest.fixture
def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    return KnowledgeBase(session_factory)


async def answer(knowledge: KnowledgeBase, question: str = QUESTION) -> list[str]:
    result = await knowledge.search(question)
    return [passage.content for passage in result.passages] if isinstance(result, Supported) else []


async def test_new_document_is_searchable_only_after_indexing(
    documents: KnowledgeDocuments, knowledge: KnowledgeBase
) -> None:
    job = await documents.save(keyboard("茶轴"))
    assert await answer(knowledge) == []

    await documents.index(job)

    assert await answer(knowledge) == ["采用热插拔茶轴。"]


async def test_new_version_leaves_search_until_it_is_indexed(
    documents: KnowledgeDocuments, knowledge: KnowledgeBase
) -> None:
    await documents.index(await documents.save(keyboard("茶轴")))

    job = await documents.save(keyboard("红轴"))
    assert (job.version, job.job) == (2, 2)
    assert await answer(knowledge) == []

    await documents.index(job)
    assert await answer(knowledge) == ["采用热插拔红轴。"]


async def test_stale_job_finishing_last_cannot_overwrite_the_new_version(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """先启动 A 的索引，A 还在算向量时上传 B，让 A 最后完成：A 什么都不写，最终只能检索到 B。"""
    embedder = PausingEmbedder()
    documents = KnowledgeDocuments(session_factory, embedder)
    job_a = await documents.save(keyboard("茶轴"))
    task_a = asyncio.create_task(documents.index(job_a))
    assert await asyncio.to_thread(embedder.started.wait, 10)

    job_b = await documents.save(keyboard("红轴"))
    embedder.release.set()
    await task_a

    assert (await documents.get(job_b.document_id)).status == "pending"  # A 既没写成已就绪，也没写成失败
    await documents.index(job_b)
    assert await answer(KnowledgeBase(session_factory, embedder)) == ["采用热插拔红轴。"]


async def test_job_finishing_after_a_newer_upload_but_before_its_indexing_writes_nothing(
    documents: KnowledgeDocuments, knowledge: KnowledgeBase
) -> None:
    job_a = await documents.save(keyboard("茶轴"))
    job_b = await documents.save(keyboard("红轴"))

    await documents.index(job_a)
    assert (await documents.get(job_a.document_id)).status == "pending"
    assert await answer(knowledge) == []

    await documents.index(job_b)
    assert await answer(knowledge) == ["采用热插拔红轴。"]


async def test_only_the_latest_reindex_of_the_same_version_takes_effect(
    documents: KnowledgeDocuments, knowledge: KnowledgeBase
) -> None:
    saved = await documents.save(keyboard("茶轴"))
    await documents.index(saved)

    first = await documents.reindex(saved.document_id)
    second = await documents.reindex(saved.document_id)
    assert (first.version, second.version, second.job) == (1, 1, first.job + 1)

    await documents.index(first)
    assert (await documents.get(saved.document_id)).status == "pending"
    assert await answer(knowledge) == []

    await documents.index(second)
    assert await answer(knowledge) == ["采用热插拔茶轴。"]


async def test_concurrent_uploads_and_reindexes_get_distinct_numbers(documents: KnowledgeDocuments) -> None:
    """同一个名称第一次就并发上传 5 次，再并发重新索引 5 次：版本和任务号都不重复、不跳号。"""
    uploads = await asyncio.gather(*(documents.save(keyboard(f"第{number}种轴")) for number in range(5)))
    assert sorted(job.version for job in uploads) == [1, 2, 3, 4, 5]
    assert sorted(job.job for job in uploads) == [1, 2, 3, 4, 5]

    reindexes = await asyncio.gather(*(documents.reindex(uploads[0].document_id) for _ in range(5)))
    assert sorted(job.job for job in reindexes) == [6, 7, 8, 9, 10]
    assert {job.version for job in reindexes} == {5}


async def test_failed_indexing_keeps_the_document_out_of_search_and_records_why(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """段落超过向量模型的上限：记为失败并写明来源，这篇不可检索，其他文档不受影响。"""
    embedder = FakeEmbedder()
    documents = KnowledgeDocuments(session_factory, embedder)
    await documents.index(await documents.save(EARBUDS))
    manual = DocumentFile("manual", "# 长说明书\n\n## 全文\n\n" + "很长的内容。" * 30 + "\n")

    job = await documents.save(manual)
    await documents.index(job)

    view = await documents.get(job.document_id)
    assert view.status == "failed"
    assert view.error is not None and "manual#全文" in view.error
    hybrid = KnowledgeBase(session_factory, embedder)
    assert await answer(hybrid, "SoundPods 的保修期多久？") == ["整机保修 1 年。"]


async def test_unexpected_error_is_recorded_as_a_failure(session_factory: async_sessionmaker[AsyncSession]) -> None:
    documents = KnowledgeDocuments(session_factory, BrokenEmbedder())

    job = await documents.save(EARBUDS)
    await documents.index(job)

    view = await documents.get(job.document_id)
    assert (view.status, view.error) == ("failed", SYSTEM_ERROR_MESSAGE)


async def test_disabling_during_indexing_stays_disabled(session_factory: async_sessionmaker[AsyncSession]) -> None:
    embedder = PausingEmbedder()
    documents = KnowledgeDocuments(session_factory, embedder)
    knowledge = KnowledgeBase(session_factory, embedder)
    job = await documents.save(keyboard("茶轴"))
    task = asyncio.create_task(documents.index(job))
    assert await asyncio.to_thread(embedder.started.wait, 10)

    await documents.set_enabled(job.document_id, False)
    embedder.release.set()
    await task

    view = await documents.get(job.document_id)
    assert (view.status, view.enabled) == ("ready", False)
    assert await answer(knowledge) == []

    await documents.set_enabled(job.document_id, True)
    assert await answer(knowledge) == ["采用热插拔茶轴。"]


async def test_startup_marks_pending_and_processing_documents_as_interrupted(
    documents: KnowledgeDocuments, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    pending = await documents.save(keyboard("茶轴"))
    processing = await documents.save(EARBUDS)
    async with session_factory.begin() as session:
        await session.execute(
            update(KnowledgeDocument).where(KnowledgeDocument.id == processing.document_id).values(status="processing")
        )
    ready = await documents.save(DocumentFile("faq", "# 常见问题\n\n## 运费\n\n所有订单包邮。\n"))
    await documents.index(ready)

    assert await documents.fail_interrupted() == 2

    views = {view.name: (view.status, view.error) for view in await documents.list_documents()}
    assert views == {
        "faq": ("ready", None),
        "keyboard-k8": ("failed", INTERRUPTED_MESSAGE),
        "soundpods": ("failed", INTERRUPTED_MESSAGE),
    }
    assert pending.document_id != processing.document_id


@pytest.mark.parametrize(
    ("name", "markdown"),
    [
        ("keyboard-k8", "## 轴体\n\n没有文档标题。\n"),
        ("keyboard-k8", "# 机械键盘 K8\n\n写在小节外面的正文。\n\n## 轴体\n\n茶轴。\n"),
        ("keyboard-k8", "# 机械键盘 K8\n\n## 轴体\n\n## 背光\n\n白色背光。\n"),
        ("keyboard-k8", "# 机械键盘 K8\n\n## 轴体\n\n茶轴。\n\n## 轴体\n\n红轴。\n"),
        ("keyboard#k8", "# 机械键盘 K8\n\n## 轴体\n\n茶轴。\n"),
        ("keyboard-k8", "# 机械键盘 K8\n\n## " + "长" * 101 + "\n\n茶轴。\n"),
    ],
    ids=["没有文档标题", "正文不在小节里", "空小节", "小节标题重复", "名称含井号", "小节标题太长"],
)
async def test_badly_formatted_document_is_rejected_and_nothing_changes(
    documents: KnowledgeDocuments, name: str, markdown: str
) -> None:
    await documents.index(await documents.save(keyboard("茶轴")))

    with pytest.raises(KnowledgeFormatError):
        await documents.save(DocumentFile(name, markdown))

    assert [(view.name, view.version, view.status) for view in await documents.list_documents()] == [
        ("keyboard-k8", 1, "ready")
    ]


async def test_vectors_and_signature_follow_the_mode_the_document_was_indexed_in(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await KnowledgeDocuments(session_factory).index(await KnowledgeDocuments(session_factory).save(keyboard("茶轴")))
    hybrid = KnowledgeDocuments(session_factory, FakeEmbedder())
    await hybrid.index(await hybrid.save(EARBUDS))

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(KnowledgeDocument.name, KnowledgeDocument.embedding_signature, KnowledgeChunk.embedding)
                .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .order_by(KnowledgeDocument.name)
            )
        ).all()
    [(keyword_name, keyword_signature, keyword_vector), (hybrid_name, hybrid_signature, hybrid_vector)] = rows
    assert (keyword_name, keyword_signature, keyword_vector) == ("keyboard-k8", None, None)
    assert hybrid_name == "soundpods"
    assert hybrid_signature is not None and hybrid_signature.startswith(FakeEmbedder.signature)
    assert len(hybrid_vector) == EMBEDDING_DIMENSION


async def test_list_shows_passage_counts_only_for_ready_documents(documents: KnowledgeDocuments) -> None:
    await documents.index(await documents.save(keyboard("茶轴")))
    await documents.save(EARBUDS)

    views = await documents.list_documents()

    assert [(view.name, view.version, view.status, view.passages, view.enabled) for view in views] == [
        ("keyboard-k8", 1, "ready", 1, True),
        ("soundpods", 1, "pending", None, True),
    ]


async def test_unknown_document_is_not_found(documents: KnowledgeDocuments) -> None:
    with pytest.raises(DocumentNotFoundError):
        await documents.reindex(999)
    with pytest.raises(DocumentNotFoundError):
        await documents.set_enabled(999, False)
