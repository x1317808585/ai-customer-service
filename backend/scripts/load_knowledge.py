"""用 backend/knowledge 下的 .md 文件整体重建知识库。

可以重复运行：每次都在一个事务里删掉旧的小节、写入新的；任何一篇格式不对，知识库保持原样。

用法（在 backend 目录下）：
    uv run python -m scripts.load_knowledge
"""

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.business.knowledge import KNOWLEDGE_DIR, KnowledgeBase, KnowledgeFormatError, read_documents
from app.config import get_settings


async def main() -> None:
    documents = read_documents()
    if not documents:
        sys.exit(f"{KNOWLEDGE_DIR} 下没有 .md 文件")

    engine = create_async_engine(get_settings().database_url)
    try:
        sections = await KnowledgeBase(async_sessionmaker(engine, expire_on_commit=False)).rebuild(documents)
    except KnowledgeFormatError as error:
        sys.exit(str(error))
    finally:
        await engine.dispose()
    print(f"已导入 {len(documents)} 篇文档，共 {sections} 个小节")


if __name__ == "__main__":
    asyncio.run(main())
