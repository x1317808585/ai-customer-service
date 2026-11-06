"""把 backend/knowledge 下的 .md 文件导入知识库。

数据库是知识文档的来源：默认只导入库里还没有的文档，不碰已有文档（管理员可能改过它们）。
加 --overwrite 才用这里的文件覆盖同名文档，覆盖也走同一套流程（内容版本加 1、重新建索引）。
导入前先检查全部文件的格式，任何一篇不对都不导入。
检索模式按 RETRIEVAL_MODE：hybrid 时会加载 BGE-M3 给段落算向量（加载模型大约要十几秒）。

用法（在 backend 目录下）：
    uv run python -m scripts.load_knowledge
    uv run python -m scripts.load_knowledge --overwrite
"""

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.business.knowledge import KNOWLEDGE_DIR, DocumentFile, KnowledgeFormatError, parse_document, read_documents
from app.business.knowledge_documents import KnowledgeDocuments
from app.config import get_settings
from app.embeddings import EmbeddingModelError, load_embedder


async def import_files(
    documents: KnowledgeDocuments, files: list[DocumentFile], overwrite: bool
) -> tuple[list[str], list[str]]:
    """导入这些文件，返回（导入或覆盖了的名称，跳过的已有名称）。每篇都建完索引才返回。

    先检查全部文件的格式，任何一篇不对就抛出 KnowledgeFormatError，一篇都不导入。
    """
    for file in files:
        parse_document(file)
    existing = {document.name for document in await documents.list_documents()}
    imported, skipped = [], []
    for file in files:
        if file.name in existing and not overwrite:
            skipped.append(file.name)
            continue
        await documents.index(await documents.save(file))
        imported.append(file.name)
    return imported, skipped


async def main(overwrite: bool) -> None:
    settings = get_settings()
    files = read_documents()
    if not files:
        sys.exit(f"{KNOWLEDGE_DIR} 下没有 .md 文件")

    print(f"检索模式：{settings.retrieval_mode}")
    try:
        embedder = load_embedder(settings)
    except EmbeddingModelError as error:
        sys.exit(str(error))

    engine = create_async_engine(settings.database_url)
    try:
        documents = KnowledgeDocuments(async_sessionmaker(engine, expire_on_commit=False), embedder)
        imported, skipped = await import_files(documents, files, overwrite)
        failed = [
            f"{document.name}：{document.error}"
            for document in await documents.list_documents()
            if document.name in imported and document.status == "failed"
        ]
    except KnowledgeFormatError as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()

    note = "（要覆盖已有文档，加 --overwrite）" if skipped else ""
    print(f"导入 {len(imported)} 篇，跳过已有的 {len(skipped)} 篇{note}")
    if failed:
        sys.exit("这些文档建索引失败：\n" + "\n".join(failed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="把 backend/knowledge 下的 .md 文件导入知识库")
    parser.add_argument("--overwrite", action="store_true", help="用这里的文件覆盖库里的同名文档")
    asyncio.run(main(parser.parse_args().overwrite))
