import itertools
import zlib
from collections.abc import Sequence

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import (
    RRF_K,
    Candidate,
    Insufficient,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFormatError,
    Passage,
    QuestionTooLongError,
    Supported,
    TextTooLongError,
    fused_order,
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
    """测试用的向量器，不加载模型。

    含有“蓝牙”或“无线”的文本落在同一个方向上（相似度 1），其余文本各自一个方向（相似度 0）。
    超过 MAX_CHARS 个字就当作超长。
    """

    signature = "fake;dim=1024"
    MAX_CHARS = 100

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = []
        for index, passage_text in enumerate(texts):
            if len(passage_text) > self.MAX_CHARS:
                raise TextTooLongError(index, len(passage_text), self.MAX_CHARS)
            same_topic = "蓝牙" in passage_text or "无线" in passage_text
            axis = 1 if same_topic else 2 + zlib.crc32(passage_text.encode()) % 1000
            vector = [0.0] * EMBEDDING_DIMENSION
            vector[axis] = 1.0
            vectors.append(vector)
        return vectors


def make_candidate(source_id: str, keyword_rank: int | None, vector_rank: int | None) -> Candidate:
    """按给定的两路名次造一个候选，RRF 分数按名次算出来。只用来测排序，判断结果随便填。"""
    ranks = [rank for rank in (keyword_rank, vector_rank) if rank is not None]
    return Candidate(
        passage=Passage(source_id=source_id, document_title="说明书", section=source_id, content="原文"),
        keyword_rank=keyword_rank,
        coverage=0.0,
        keyword_ok=False,
        vector_rank=vector_rank,
        similarity=None,
        exact_ok=True,
        rrf=sum(1 / (RRF_K + rank) for rank in ranks),
    )


@pytest.fixture
def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    return KnowledgeBase(session_factory)


@pytest.fixture
async def hybrid(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    hybrid = KnowledgeBase(session_factory, FakeEmbedder())
    await hybrid.rebuild([KEYBOARD, EARBUDS])
    return hybrid


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


async def test_vector_match_above_the_threshold_is_evidence_without_shared_words(hybrid: KnowledgeBase) -> None:
    """问题里没有一个词出现在原文里，关键词这一路找不到；向量足够接近，照样算依据。"""
    result = await hybrid.search("无线配对的办法？")

    assert isinstance(result, Supported)
    assert [passage.source_id for passage in result.passages] == ["keyboard-k8#连接方式"]


async def test_vector_match_below_the_threshold_is_insufficient(hybrid: KnowledgeBase) -> None:
    assert await hybrid.search("配对的办法？") == Insufficient()


async def test_model_names_must_match_exactly_on_the_vector_path_too(hybrid: KnowledgeBase) -> None:
    """向量再接近，问 SoundPods 的问题也不能拿 K8 的小节来回答。"""
    candidates = await hybrid.candidates("SoundPods 无线怎么配对？")

    assert any(candidate.similarity == 1.0 for candidate in candidates)
    assert await hybrid.search("SoundPods 无线怎么配对？") == Insufficient()


async def test_hybrid_search_refuses_a_knowledge_base_built_another_way(
    knowledge: KnowledgeBase, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """知识库是按关键词模式重建的，没有向量：报错，不当作“没有依据”。"""
    await knowledge.rebuild([KEYBOARD])
    hybrid = KnowledgeBase(session_factory, FakeEmbedder())

    with pytest.raises(RuntimeError, match="对不上"):
        await hybrid.check_index()
    with pytest.raises(RuntimeError, match="对不上"):
        await hybrid.search("K8 是什么轴？")


async def test_question_too_long_for_the_model_is_a_business_error(hybrid: KnowledgeBase) -> None:
    with pytest.raises(QuestionTooLongError, match="请精简后再查"):
        await hybrid.search("蓝牙" * 60)


def test_swapped_ranks_tie_goes_to_the_better_keyword_rank() -> None:
    """关键词第 1、向量第 2 和关键词第 2、向量第 1 的 RRF 完全相同，关键词名次靠前的排第一。

    只按来源标识排的话，“快充功率”会排在“线身”前面，所以这里起作用的是关键词名次。
    """
    keyword_first = make_candidate("cable#线身", keyword_rank=1, vector_rank=2)
    vector_first = make_candidate("cable#快充功率", keyword_rank=2, vector_rank=1)

    assert keyword_first.rrf == vector_first.rrf
    assert fused_order([vector_first, keyword_first]) == [keyword_first, vector_first]


def test_candidate_found_only_by_vectors_ranks_after_an_equal_keyword_candidate() -> None:
    """只被向量召回的第 1 名和只被关键词召回的第 1 名 RRF 相同，有关键词名次的排在前面。"""
    vector_only = make_candidate("a#向量", keyword_rank=None, vector_rank=1)
    keyword_only = make_candidate("z#关键词", keyword_rank=1, vector_rank=None)

    assert vector_only.rrf == keyword_only.rrf
    assert fused_order([vector_only, keyword_only]) == [keyword_only, vector_only]


def test_final_order_does_not_depend_on_the_order_of_the_candidate_list() -> None:
    """只证明最终排序稳定：两路名次固定以后，候选列表怎么排都得到同一个结果。

    关键词这一路内部覆盖率相同时仍按写入顺序排名次，所以证明不了“重新排列知识文档后，整个检索结果都不变”。
    """
    candidates = [
        make_candidate("a#一", keyword_rank=1, vector_rank=3),
        make_candidate("b#二", keyword_rank=3, vector_rank=1),
        make_candidate("c#三", keyword_rank=2, vector_rank=2),
        make_candidate("d#四", keyword_rank=None, vector_rank=4),
        make_candidate("e#五", keyword_rank=4, vector_rank=None),
    ]
    expected = fused_order(candidates)

    for permutation in itertools.permutations(candidates):
        assert fused_order(list(permutation)) == expected
