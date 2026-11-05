from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import (
    Insufficient,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFormatError,
    Passage,
    Supported,
    TextTooLongError,
)
from app.models import EMBEDDING_DIMENSION, KnowledgeChunk, KnowledgeIndex

KEYBOARD = KnowledgeDocument(
    "keyboard-k8",
    "# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔茶轴，段落感适中。\n\n## 连接方式\n\n支持有线和蓝牙双模连接。\n",
)
EARBUDS = KnowledgeDocument(
    "soundpods",
    "# SoundPods 降噪耳机说明书\n\n## 保修\n\n整机保修 1 年，从签收之日起计算。\n",
)


class FakeEmbedder:
    """测试用的向量器：不加载模型，每段文字都给同一个单位向量；超过 MAX_CHARS 个字就当作超长。"""

    signature = "fake;dim=1024"
    MAX_CHARS = 100

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        for index, passage_text in enumerate(texts):
            if len(passage_text) > self.MAX_CHARS:
                raise TextTooLongError(index, len(passage_text), self.MAX_CHARS)
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]


@pytest.fixture
def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    return KnowledgeBase(session_factory)


async def test_answers_with_the_section_and_its_source(knowledge: KnowledgeBase) -> None:
    await knowledge.rebuild([KEYBOARD, EARBUDS])

    result = await knowledge.search("K8 是什么轴？")

    assert isinstance(result, Supported)
    assert result.passages[0] == Passage(
        source_id="keyboard-k8#轴体",
        document_title="机械键盘 K8 说明书",
        section="轴体",
        content="采用热插拔茶轴，段落感适中。",
    )


async def test_model_names_must_match_exactly(knowledge: KnowledgeBase) -> None:
    """K8 的说明书里没写保修，不能拿 SoundPods 的保修条款来回答。"""
    await knowledge.rebuild([KEYBOARD, EARBUDS])

    assert await knowledge.search("K8 的保修期多久？") == Insufficient()
    assert isinstance(await knowledge.search("SoundPods 的保修期多久？"), Supported)


async def test_right_document_but_unanswered_attribute_is_insufficient(knowledge: KnowledgeBase) -> None:
    """K8 说明书排在最前也不够：它没写能同时连几台设备。"""
    await knowledge.rebuild([KEYBOARD, EARBUDS])

    assert await knowledge.search("K8 能同时连接几台设备？") == Insufficient()


async def test_question_without_meaningful_words_is_insufficient(knowledge: KnowledgeBase) -> None:
    await knowledge.rebuild([KEYBOARD])

    assert await knowledge.search("请问一下？") == Insufficient()


async def test_rebuild_replaces_the_whole_knowledge_base(knowledge: KnowledgeBase) -> None:
    await knowledge.rebuild([KEYBOARD, EARBUDS])
    new_keyboard = KnowledgeDocument("keyboard-k8", "# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔红轴。\n")

    assert await knowledge.rebuild([new_keyboard]) == 1

    result = await knowledge.search("K8 是什么轴？")
    assert isinstance(result, Supported)
    assert [passage.content for passage in result.passages] == ["采用热插拔红轴。"]
    assert await knowledge.search("SoundPods 的保修期多久？") == Insufficient()


@pytest.mark.parametrize(
    "markdown",
    [
        "## 轴体\n\n没有文档标题。\n",
        "# 机械键盘 K8\n\n写在小节外面的正文。\n\n## 轴体\n\n茶轴。\n",
        "# 机械键盘 K8\n\n## 轴体\n\n## 背光\n\n白色背光。\n",
        "# 机械键盘 K8\n\n## 轴体\n\n茶轴。\n\n## 轴体\n\n红轴。\n",
    ],
    ids=["没有文档标题", "正文不在小节里", "空小节", "小节标题重复"],
)
async def test_badly_formatted_document_leaves_the_knowledge_base_unchanged(
    knowledge: KnowledgeBase, markdown: str
) -> None:
    await knowledge.rebuild([KEYBOARD])

    with pytest.raises(KnowledgeFormatError):
        await knowledge.rebuild([EARBUDS, KnowledgeDocument("broken", markdown)])

    assert isinstance(await knowledge.search("K8 是什么轴？"), Supported)


async def test_database_failure_is_an_error_not_insufficient(
    knowledge: KnowledgeBase, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory.begin() as session:
        await session.execute(text("DROP TABLE knowledge_chunks"))

    with pytest.raises(DBAPIError):
        await knowledge.search("K8 是什么轴？")


async def test_keyword_rebuild_records_keyword_mode_and_stores_no_vectors(
    knowledge: KnowledgeBase, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await knowledge.rebuild([KEYBOARD, EARBUDS])

    async with session_factory() as session:
        embeddings = (await session.scalars(select(KnowledgeChunk.embedding))).all()
        index = await session.get_one(KnowledgeIndex, 1)
    assert embeddings == [None, None, None]
    assert (index.mode, index.embedding_signature) == ("keyword", None)


async def test_hybrid_rebuild_stores_a_vector_for_every_section_and_records_its_signature(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await KnowledgeBase(session_factory, FakeEmbedder()).rebuild([KEYBOARD, EARBUDS])

    async with session_factory() as session:
        embeddings = (await session.scalars(select(KnowledgeChunk.embedding))).all()
        index = await session.get_one(KnowledgeIndex, 1)
    assert [len(embedding) for embedding in embeddings] == [EMBEDDING_DIMENSION] * 3
    assert index.mode == "hybrid"
    assert index.embedding_signature is not None
    assert index.embedding_signature.startswith(FakeEmbedder.signature)


async def test_section_too_long_for_the_model_is_reported_and_leaves_the_knowledge_base_unchanged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    knowledge = KnowledgeBase(session_factory, FakeEmbedder())
    await knowledge.rebuild([KEYBOARD])
    manual = KnowledgeDocument("manual", "# 长说明书\n\n## 全文\n\n" + "很长的内容。" * 30 + "\n")

    with pytest.raises(KnowledgeFormatError, match="manual#全文"):
        await knowledge.rebuild([EARBUDS, manual])

    assert isinstance(await knowledge.search("K8 是什么轴？"), Supported)
