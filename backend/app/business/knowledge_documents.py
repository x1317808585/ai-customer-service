"""知识文档的保存和建索引。上传接口和导入脚本共用这里，内容版本和索引任务的规则集中在这里。

一致性规则：
- 能被检索 = 启用 + 已就绪。上传新内容或安排重新索引后，这篇文档马上退出检索，建好索引才回来。
- 每次上传新内容，内容版本加 1；每次安排建索引，索引任务号加 1。两者都由数据库原子递增，
  程序里不先读旧值再写回，并发请求不会拿到相同的任务号。
- 每个索引任务固定了（文档、内容版本、任务号）。开始时和提交时都锁住文档再核对一次，
  已经过期（又上传了新内容，或者又安排了新任务）就什么都不写：不写段落，也不改状态。
  所以旧任务再晚完成，也覆盖不了新版本。
- 任务从不修改“是否启用”：建索引过程中被停用的文档，建好后仍然是停用。
- 同一个进程里一次只建一篇（信号量）。

限制：任务在后端进程里执行，只适用于单个后端进程（见 fail_interrupted）。第 19 步部署前必须按实际启动方式重新处理。
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.business.knowledge import (
    DocumentFile,
    Embedder,
    KnowledgeFormatError,
    Passage,
    TextTooLongError,
    document_terms,
    embedding_signature,
    parse_document,
    passage_text,
)
from app.models import KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentStatus

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = "处理被中断（服务重启），请重新索引"
SYSTEM_ERROR_MESSAGE = "建索引时系统出错，请重新索引"
MAX_ERROR_CHARS = 500  # 失败原因最多保存多少字（数据库字段长度）

# 安排建索引时一起清掉的字段：文档马上退出检索，等任务建好再写回
_UNINDEXED: dict[str, Any] = {"status": "pending", "error": None, "indexed_version": None, "embedding_signature": None}


class DocumentNotFoundError(BusinessError):
    """要操作的知识文档不存在。"""


@dataclass(frozen=True)
class IndexJob:
    """一次建索引任务：固定了文档、内容版本和任务号。提交前核对它们仍是最新的，过期任务什么都不写。"""

    document_id: int
    version: int
    job: int


class DocumentView(BaseModel):
    """管理工作台里看到的一篇知识文档。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    title: str
    version: int
    status: KnowledgeDocumentStatus
    error: str | None
    enabled: bool
    passages: int | None = None  # 已就绪时的段落数；其他状态为 None
    indexed_at: datetime | None
    updated_at: datetime


class KnowledgeDocuments:
    """知识文档的管理。不传向量器是关键词模式；传了是混合模式，建索引时给每个段落算向量。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder | None = None) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._signature = embedding_signature(embedder)
        self._indexing = asyncio.Semaphore(1)  # 同一个进程里一次只建一篇

    async def save(self, file: DocumentFile) -> IndexJob:
        """保存一篇文档（同名就是更新）并安排建索引，返回要交给 index 执行的任务。

        格式不对抛出 KnowledgeFormatError，什么都不存。新文档的内容版本和任务号都从 1 开始；
        同名更新时两者由数据库原子加 1（INSERT … ON CONFLICT），同一个名称首次上传的并发请求也由唯一约束兜住。
        “是否启用”不变。
        """
        parsed = parse_document(file)
        table = KnowledgeDocument.__table__
        statement = (
            insert(KnowledgeDocument)
            .values(
                name=file.name,
                title=parsed.title,
                markdown=file.markdown,
                version=1,
                index_job=1,
                status="pending",
            )
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "title": parsed.title,
                    "markdown": file.markdown,
                    "version": table.c.version + 1,
                    "index_job": table.c.index_job + 1,
                    "updated_at": func.now(),
                    **_UNINDEXED,
                },
            )
            .returning(KnowledgeDocument.id, KnowledgeDocument.version, KnowledgeDocument.index_job)
        )
        async with self._session_factory.begin() as session:
            document_id, version, job = (await session.execute(statement)).one()
        return IndexJob(document_id, version, job)

    async def reindex(self, document_id: int) -> IndexJob:
        """安排重新建索引：任务号由数据库原子加 1，文档马上退出检索，之前安排的任务随之作废。内容版本不变。"""
        statement = (
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(index_job=KnowledgeDocument.index_job + 1, **_UNINDEXED)
            .returning(KnowledgeDocument.id, KnowledgeDocument.version, KnowledgeDocument.index_job)
            .execution_options(synchronize_session=False)
        )
        async with self._session_factory.begin() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            raise DocumentNotFoundError(f"知识文档 {document_id} 不存在")
        return IndexJob(*row)

    async def index(self, job: IndexJob) -> None:
        """执行一个建索引任务。任务过期时什么都不做；任务仍有效但出错时，记为失败并写明原因。

        供后台任务调用，所以自己处理异常：后台任务里抛出的异常只会进日志，文档会一直停在“处理中”。
        """
        async with self._indexing:
            try:
                passages = await self._start(job)
                if passages is None:
                    return
                embeddings = await self._embed(passages)
                await self._finish(job, passages, embeddings)
            except Exception as error:
                if isinstance(error, BusinessError):
                    message = str(error)
                else:
                    logger.exception("知识文档 %s 建索引出错", job.document_id)
                    message = SYSTEM_ERROR_MESSAGE
                await self._fail(job, message)

    async def set_enabled(self, document_id: int, enabled: bool) -> DocumentView:
        """启用或停用。提交后下一次检索就生效；不影响正在进行的索引任务，任务完成后也不会把它改回来。"""
        statement = (
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(enabled=enabled)
            .returning(KnowledgeDocument.id)
            .execution_options(synchronize_session=False)
        )
        async with self._session_factory.begin() as session:
            if (await session.execute(statement)).one_or_none() is None:
                raise DocumentNotFoundError(f"知识文档 {document_id} 不存在")
        return await self.get(document_id)

    async def get(self, document_id: int) -> DocumentView:
        views = await self._views(KnowledgeDocument.id == document_id)
        if not views:
            raise DocumentNotFoundError(f"知识文档 {document_id} 不存在")
        return views[0]

    async def list_documents(self) -> list[DocumentView]:
        """全部知识文档，按名称排序。"""
        return await self._views()

    async def fail_interrupted(self) -> int:
        """后端启动时调用：把还停在“待处理”“处理中”的文档都标成失败，返回篇数，管理员可以重新索引。

        没有自动重排，是为了避免某篇文档每次处理都把进程弄挂、重启后又反复重试。
        这假设只有一个后端进程：多进程部署时，一个进程启动会把别的进程正在处理的任务误判为失败。
        第 19 步部署前必须按实际启动方式重新处理。
        """
        statement = (
            update(KnowledgeDocument)
            .where(KnowledgeDocument.status.in_(["pending", "processing"]))
            .values(status="failed", error=INTERRUPTED_MESSAGE)
            .returning(KnowledgeDocument.id)
            .execution_options(synchronize_session=False)
        )
        async with self._session_factory.begin() as session:
            return len((await session.execute(statement)).all())

    async def _start(self, job: IndexJob) -> list[Passage] | None:
        """任务仍有效时改成“处理中”，返回要建索引的段落；过期返回 None。"""
        async with self._session_factory.begin() as session:
            document = await self._current(session, job)
            if document is None:
                return None
            document.status = "processing"
            return parse_document(DocumentFile(document.name, document.markdown)).passages

    async def _finish(self, job: IndexJob, passages: list[Passage], embeddings: list[list[float] | None]) -> None:
        """任务仍有效时，在一个事务里替换这篇文档的段落、写入签名和已索引版本，并改成“已就绪”。"""
        async with self._session_factory.begin() as session:
            document = await self._current(session, job)
            if document is None:
                return
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == job.document_id))
            session.add_all(
                KnowledgeChunk(
                    document_id=job.document_id,
                    position=position,
                    **passage.model_dump(),
                    search_vector=func.array_to_tsvector(sorted(document_terms(passage_text(passage)))),
                    embedding=embedding,
                )
                for position, (passage, embedding) in enumerate(zip(passages, embeddings, strict=True))
            )
            document.status = "ready"
            document.error = None
            document.indexed_version = job.version
            document.embedding_signature = self._signature
            document.indexed_at = func.now()

    async def _fail(self, job: IndexJob, message: str) -> None:
        """任务仍有效时记为失败；过期任务不回写失败状态。"""
        async with self._session_factory.begin() as session:
            document = await self._current(session, job)
            if document is None:
                return
            document.status = "failed"
            document.error = message[:MAX_ERROR_CHARS]

    @staticmethod
    async def _current(session: AsyncSession, job: IndexJob) -> KnowledgeDocument | None:
        """锁住文档（SELECT … FOR UPDATE），核对任务仍是最新的：内容版本和任务号都没变。过期返回 None。"""
        document = await session.get(KnowledgeDocument, job.document_id, with_for_update=True)
        if document is None or (document.version, document.index_job) != (job.version, job.job):
            return None
        return document

    async def _embed(self, passages: list[Passage]) -> list[list[float] | None]:
        """关键词模式下不算向量；混合模式下一次算完全部段落，超长的段落报出来源。"""
        if self._embedder is None:
            return [None] * len(passages)
        try:
            # 编码是 CPU 密集的同步计算，放到线程里做，不阻塞事件循环
            return await asyncio.to_thread(self._embedder.embed, [passage_text(passage) for passage in passages])
        except TextTooLongError as error:
            raise KnowledgeFormatError(
                f"{passages[error.index].source_id}：有 {error.tokens} 个 token，"
                f"超过向量模型的上限 {error.limit}，请拆成更短的小节"
            ) from error

    async def _views(self, *conditions: Any) -> list[DocumentView]:
        counts = (
            select(KnowledgeChunk.document_id, func.count().label("passages"))
            .group_by(KnowledgeChunk.document_id)
            .subquery()
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(KnowledgeDocument, counts.c.passages)
                    .outerjoin(counts, counts.c.document_id == KnowledgeDocument.id)
                    .where(*conditions)
                    .order_by(KnowledgeDocument.name)
                )
            ).all()
        return [
            DocumentView.model_validate(document).model_copy(
                update={"passages": passages if document.status == "ready" else None}
            )
            for document, passages in rows
        ]
