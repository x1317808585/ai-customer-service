"""管理员的知识库接口：上传、后台建索引、启用和停用、重新索引。权限检查由 test_access.py 统一覆盖。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import DocumentFile, Insufficient, KnowledgeBase, Supported
from app.business.knowledge_documents import INTERRUPTED_MESSAGE, KnowledgeDocuments

OpenClient = Callable[..., AbstractAsyncContextManager[AsyncClient]]
SessionFactory = async_sessionmaker[AsyncSession]

DOCUMENTS = "/api/admin/knowledge/documents"
KEYBOARD = "# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔茶轴。\n\n## 连接方式\n\n支持有线和蓝牙双模连接。\n"
QUESTION = "K8 是什么轴？"


def upload(filename: str, content: str | bytes) -> dict[str, tuple[str, bytes, str]]:
    data = content.encode() if isinstance(content, str) else content
    return {"file": (filename, data, "text/markdown")}


async def listed(admin: AsyncClient) -> list[tuple[str, int, str, int | None, bool]]:
    documents = (await admin.get(DOCUMENTS)).json()
    return [(d["name"], d["version"], d["status"], d["passages"], d["enabled"]) for d in documents]


async def test_uploaded_document_is_indexed_in_the_background(
    open_client: OpenClient, session_factory: SessionFactory
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(DOCUMENTS, files=upload("keyboard-k8.md", KEYBOARD))
        # 接口返回时还没建索引；测试客户端会等后台任务做完才返回，所以接着查列表已经是已就绪
        assert response.status_code == 202
        assert (response.json()["name"], response.json()["status"]) == ("keyboard-k8", "pending")
        assert await listed(admin) == [("keyboard-k8", 1, "ready", 2, True)]

        await admin.post(DOCUMENTS, files=upload("keyboard-k8.md", KEYBOARD.replace("茶轴", "红轴")))
        assert await listed(admin) == [("keyboard-k8", 2, "ready", 2, True)]

    result = await KnowledgeBase(session_factory).search(QUESTION)
    assert isinstance(result, Supported)
    assert result.passages[0].content == "采用热插拔红轴。"


@pytest.mark.parametrize(
    ("filename", "content", "detail"),
    [
        ("keyboard-k8.txt", KEYBOARD, "只支持 .md 文件"),
        ("keyboard-k8.md", "没有文档标题。\n", "第一行必须是"),
        ("keyboard-k8.md", KEYBOARD.encode("gbk"), "UTF-8"),
        ("keyboard-k8.md", b"#" * 1_000_001, "1MB"),
    ],
    ids=["不是md", "格式不对", "不是UTF-8", "超过1MB"],
)
async def test_rejected_upload_saves_nothing(
    open_client: OpenClient, filename: str, content: str | bytes, detail: str
) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(DOCUMENTS, files=upload(filename, content))

        assert response.status_code == 422
        assert detail in response.json()["detail"]
        assert await listed(admin) == []


async def test_disabling_and_enabling_take_effect_on_the_next_search(
    open_client: OpenClient, session_factory: SessionFactory
) -> None:
    knowledge = KnowledgeBase(session_factory)
    async with open_client(as_user="admin") as admin:
        document_id = (await admin.post(DOCUMENTS, files=upload("keyboard-k8.md", KEYBOARD))).json()["id"]

        disabled = await admin.post(f"{DOCUMENTS}/{document_id}/disable")
        assert (disabled.status_code, disabled.json()["enabled"]) == (200, False)
        assert await knowledge.search(QUESTION) == Insufficient()

        enabled = await admin.post(f"{DOCUMENTS}/{document_id}/enable")
        assert (enabled.status_code, enabled.json()["enabled"]) == (200, True)
        assert isinstance(await knowledge.search(QUESTION), Supported)


async def test_reindex_answers_at_once_and_indexes_in_the_background(open_client: OpenClient) -> None:
    async with open_client(as_user="admin") as admin:
        document_id = (await admin.post(DOCUMENTS, files=upload("keyboard-k8.md", KEYBOARD))).json()["id"]

        response = await admin.post(f"{DOCUMENTS}/{document_id}/reindex")

        assert (response.status_code, response.json()["status"]) == (202, "pending")
        assert await listed(admin) == [("keyboard-k8", 1, "ready", 2, True)]


@pytest.mark.parametrize("action", ["reindex", "enable", "disable"])
async def test_unknown_document_is_not_found(open_client: OpenClient, action: str) -> None:
    async with open_client(as_user="admin") as admin:
        response = await admin.post(f"{DOCUMENTS}/999/{action}")

    assert response.status_code == 404


async def test_backend_startup_marks_unfinished_documents_as_failed(
    open_client: OpenClient, session_factory: SessionFactory
) -> None:
    await KnowledgeDocuments(session_factory).save(DocumentFile("keyboard-k8", KEYBOARD))  # 安排了任务，还没执行

    async with open_client(as_user="admin") as admin:
        [document] = (await admin.get(DOCUMENTS)).json()

    assert (document["status"], document["error"]) == ("failed", INTERRUPTED_MESSAGE)
