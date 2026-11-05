"""用 backend/knowledge 下的 .md 文件整体重建知识库。

可以重复运行：每次都在一个事务里删掉旧的小节、写入新的；任何一篇格式不对，知识库保持原样。
检索模式按 RETRIEVAL_MODE：hybrid 时会加载 BGE-M3，给每个小节算向量（加载模型大约要十几秒）。

用法（在 backend 目录下）：
    uv run python -m scripts.load_knowledge
"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.business.knowledge import KNOWLEDGE_DIR, KnowledgeBase, KnowledgeFormatError, read_documents
from app.config import get_settings
from app.embeddings import EmbeddingModelError, load_embedder


async def main() -> None:
    settings = get_settings()
    documents = read_documents()
    if not documents:
        sys.exit(f"{KNOWLEDGE_DIR} 下没有 .md 文件")

    print(f"检索模式：{settings.retrieval_mode}")
    try:
        embedder = load_embedder(settings)
    except EmbeddingModelError as error:
        sys.exit(str(error))

    engine = create_async_engine(settings.database_url)
    try:
        knowledge = KnowledgeBase(async_sessionmaker(engine, expire_on_commit=False), embedder)
        sections = await knowledge.rebuild(documents)
    except KnowledgeFormatError as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()
    print(f"已导入 {len(documents)} 篇文档，共 {sections} 个小节")


if __name__ == "__main__":
    asyncio.run(main())
