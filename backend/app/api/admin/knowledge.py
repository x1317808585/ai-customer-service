"""管理员的知识库接口：上传 Markdown 文档、查看处理状态、启用和停用、重新索引。

上传和重新索引只安排任务并立即返回 202，建索引在后台任务里进行（规则见 app/business/knowledge_documents.py）。
"""

from collections.abc import Awaitable

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status

from app.api.dependencies import KnowledgeDocumentsDep
from app.business.knowledge import DocumentFile, KnowledgeFormatError
from app.business.knowledge_documents import DocumentNotFoundError, DocumentView

router = APIRouter(prefix="/knowledge/documents")

MAX_UPLOAD_BYTES = 1_000_000  # 上传的文件最大 1MB
MARKDOWN_SUFFIX = ".md"


@router.get("")
async def list_documents(documents: KnowledgeDocumentsDep) -> list[DocumentView]:
    return await documents.list_documents()


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile, documents: KnowledgeDocumentsDep, background_tasks: BackgroundTasks
) -> DocumentView:
    """上传一篇 Markdown 文档，文件名去掉 .md 就是文档名称，同名就是更新。

    文件不合格（不是 .md、太大、不是 UTF-8、格式不对）返回 422，什么都不存；合格就安排建索引，立即返回。
    """
    filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not filename.lower().endswith(MARKDOWN_SUFFIX):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "只支持 .md 文件")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "文件不能超过 1MB")
    try:
        markdown = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "文件必须是 UTF-8 编码") from error

    try:
        job = await documents.save(DocumentFile(filename[: -len(MARKDOWN_SUFFIX)], markdown))
    except KnowledgeFormatError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    background_tasks.add_task(documents.index, job)
    return await documents.get(job.document_id)


@router.post("/{document_id}/reindex", status_code=status.HTTP_202_ACCEPTED)
async def reindex_document(
    document_id: int, documents: KnowledgeDocumentsDep, background_tasks: BackgroundTasks
) -> DocumentView:
    """重新建索引：文档马上退出检索，建好后回来。之前安排的任务随之作废。"""
    job = await _found(documents.reindex(document_id))
    background_tasks.add_task(documents.index, job)
    return await documents.get(document_id)


@router.post("/{document_id}/enable")
async def enable_document(document_id: int, documents: KnowledgeDocumentsDep) -> DocumentView:
    return await _found(documents.set_enabled(document_id, True))


@router.post("/{document_id}/disable")
async def disable_document(document_id: int, documents: KnowledgeDocumentsDep) -> DocumentView:
    """停用后下一次检索就生效。"""
    return await _found(documents.set_enabled(document_id, False))


async def _found[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except DocumentNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
