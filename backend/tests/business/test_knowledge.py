import itertools

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import (
    MAX_PART_CHARS,
    RRF_K,
    Candidate,
    DocumentFile,
    Insufficient,
    KnowledgeBase,
    Passage,
    QuestionTooLongError,
    Supported,
    fused_order,
    parse_document,
)
from app.business.knowledge_documents import KnowledgeDocuments
from tests.business.fake_embedder import FakeEmbedder
from tests.conftest import LoadKnowledge

KEYBOARD = DocumentFile(
    "keyboard-k8",
    "# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔茶轴，段落感适中。\n\n## 连接方式\n\n支持有线和蓝牙双模连接。\n",
)
EARBUDS = DocumentFile(
    "soundpods",
    "# SoundPods 降噪耳机说明书\n\n## 保修\n\n整机保修 1 年，从签收之日起计算。\n",
)
KEYBOARD_K9 = DocumentFile("keyboard-k9", "# 机械键盘 K9 说明书\n\n## 保修\n\n整机保修 2 年。\n")


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


async def document_id(session_factory: async_sessionmaker[AsyncSession], name: str) -> int:
    [document] = [d for d in await KnowledgeDocuments(session_factory).list_documents() if d.name == name]
    return document.id


@pytest.fixture
def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    return KnowledgeBase(session_factory)


@pytest.fixture
async def hybrid(session_factory: async_sessionmaker[AsyncSession], load_knowledge: LoadKnowledge) -> KnowledgeBase:
    await load_knowledge([KEYBOARD, EARBUDS], FakeEmbedder())
    return KnowledgeBase(session_factory, FakeEmbedder())


async def test_answers_with_the_section_and_its_source(knowledge: KnowledgeBase, load_knowledge: LoadKnowledge) -> None:
    await load_knowledge([KEYBOARD, EARBUDS])

    result = await knowledge.search("K8 是什么轴？")

    assert isinstance(result, Supported)
    assert result.passages[0] == Passage(
        source_id="keyboard-k8#轴体",
        document_title="机械键盘 K8 说明书",
        section="轴体",
        content="采用热插拔茶轴，段落感适中。",
    )


async def test_model_names_must_match_exactly(knowledge: KnowledgeBase, load_knowledge: LoadKnowledge) -> None:
    """K8 的说明书里没写保修，不能拿 SoundPods 的保修条款来回答。"""
    await load_knowledge([KEYBOARD, EARBUDS])

    assert await knowledge.search("K8 的保修期多久？") == Insufficient()
    assert isinstance(await knowledge.search("SoundPods 的保修期多久？"), Supported)


async def test_right_document_but_unanswered_attribute_is_insufficient(
    knowledge: KnowledgeBase, load_knowledge: LoadKnowledge
) -> None:
    """K8 说明书排在最前也不够：它没写能同时连几台设备。"""
    await load_knowledge([KEYBOARD, EARBUDS])

    assert await knowledge.search("K8 能同时连接几台设备？") == Insufficient()


async def test_question_without_meaningful_words_is_insufficient(
    knowledge: KnowledgeBase, load_knowledge: LoadKnowledge
) -> None:
    await load_knowledge([KEYBOARD])

    assert await knowledge.search("请问一下？") == Insufficient()


async def test_updating_a_document_replaces_only_its_own_passages(
    knowledge: KnowledgeBase, load_knowledge: LoadKnowledge
) -> None:
    await load_knowledge([KEYBOARD, EARBUDS])

    await load_knowledge([DocumentFile("keyboard-k8", "# 机械键盘 K8 说明书\n\n## 轴体\n\n采用热插拔红轴。\n")])

    result = await knowledge.search("K8 是什么轴？")
    assert isinstance(result, Supported)
    assert [passage.content for passage in result.passages] == ["采用热插拔红轴。"]
    assert isinstance(await knowledge.search("SoundPods 的保修期多久？"), Supported)


async def test_database_failure_is_an_error_not_insufficient(
    knowledge: KnowledgeBase, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory.begin() as session:
        await session.execute(text("DROP TABLE knowledge_chunks"))

    with pytest.raises(DBAPIError):
        await knowledge.search("K8 是什么轴？")


async def test_disabled_document_is_not_searched_and_does_not_count_towards_word_weights(
    knowledge: KnowledgeBase, load_knowledge: LoadKnowledge, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """停用的 K9 说明书里也有“保修”。停用后，SoundPods 那道题的候选和分数应该和 K9 从没导入过时完全一样。"""
    await load_knowledge([KEYBOARD, EARBUDS])
    before = await knowledge.candidates("SoundPods 的保修期多久？")
    await load_knowledge([KEYBOARD_K9])

    await KnowledgeDocuments(session_factory).set_enabled(await document_id(session_factory, "keyboard-k9"), False)

    assert await knowledge.candidates("SoundPods 的保修期多久？") == before
    assert await knowledge.search("K9 的保修期多久？") == Insufficient()


async def test_equal_keyword_scores_are_ordered_by_document_name_not_by_indexing_order(
    knowledge: KnowledgeBase, load_knowledge: LoadKnowledge
) -> None:
    """两篇文档的段落一模一样，覆盖率并列。后建索引的 a-keyboard 数据库 ID 更大，仍然按名称排在前面。"""
    switch = "# 键盘说明书\n\n## 轴体\n\n茶轴。\n"
    await load_knowledge([DocumentFile("b-keyboard", switch), DocumentFile("a-keyboard", switch)])

    candidates = await knowledge.candidates("茶轴")

    assert [candidate.passage.source_id for candidate in candidates] == ["a-keyboard#轴体", "b-keyboard#轴体"]


async def test_vector_match_above_the_threshold_is_evidence_without_shared_words(hybrid: KnowledgeBase) -> None:
    """问题里没有一个词出现在原文里，关键词这一路找不到；向量足够接近，照样算依据。"""
    result = await hybrid.search("无线配对的办法？")

    assert isinstance(result, Supported)
    assert [passage.source_id for passage in result.passages] == ["keyboard-k8#连接方式"]


async def test_vector_match_below_the_threshold_is_insufficient(hybrid: KnowledgeBase) -> None:
    assert await hybrid.search("配对的办法？") == Insufficient()


async def test_model_names_must_match_exactly_on_the_vector_path_too(hybrid: KnowledgeBase) -> None:
    """向量再接近，问 SoundPods 的问题也不能拿 K8 的段落来回答。"""
    candidates = await hybrid.candidates("SoundPods 无线怎么配对？")

    assert any(candidate.similarity == 1.0 for candidate in candidates)
    assert await hybrid.search("SoundPods 无线怎么配对？") == Insufficient()


async def test_question_too_long_for_the_model_is_a_business_error(hybrid: KnowledgeBase) -> None:
    with pytest.raises(QuestionTooLongError, match="请精简后再查"):
        await hybrid.search("蓝牙" * 60)


async def test_hybrid_search_refuses_documents_indexed_another_way_unless_they_are_disabled(
    load_knowledge: LoadKnowledge, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """按关键词模式建的索引没有向量：混合检索报错，不当作“没有依据”。停用这篇文档后，它的旧签名不再挡住检索。"""
    await load_knowledge([KEYBOARD])
    hybrid = KnowledgeBase(session_factory, FakeEmbedder())

    with pytest.raises(RuntimeError, match="对不上"):
        await hybrid.check_index()
    with pytest.raises(RuntimeError, match="对不上"):
        await hybrid.search("K8 是什么轴？")

    await KnowledgeDocuments(session_factory).set_enabled(await document_id(session_factory, "keyboard-k8"), False)
    await hybrid.check_index()
    assert await hybrid.search("K8 是什么轴？") == Insufficient()


def test_short_section_is_one_passage() -> None:
    parsed = parse_document(KEYBOARD)

    assert parsed.title == "机械键盘 K8 说明书"
    assert [passage.source_id for passage in parsed.passages] == ["keyboard-k8#轴体", "keyboard-k8#连接方式"]


def test_long_section_is_split_at_sentence_boundaries_with_overlap() -> None:
    """30 句、每句 45 字：切成几块，每块不超过上限，下一块以上一块的最后一句开头，每一句都没丢。"""
    sentences = [f"第{number:02d}句" + "内容" * 20 + "。" for number in range(30)]
    parsed = parse_document(DocumentFile("manual", "# 说明书\n\n## 全文\n\n" + "".join(sentences) + "\n"))
    parts = [passage.content for passage in parsed.passages]

    assert len(parts) >= 3
    assert all(len(part) <= MAX_PART_CHARS for part in parts)
    assert [passage.source_id for passage in parsed.passages] == [
        f"manual#全文#{number}" for number in range(1, len(parts) + 1)
    ]
    for previous, current in itertools.pairwise(parts):
        assert current[:45] == previous[-45:]
    assert all(any(sentence in part for part in parts) for sentence in sentences)


def test_long_text_without_punctuation_is_cut_at_the_limit() -> None:
    parsed = parse_document(DocumentFile("manual", "# 说明书\n\n## 全文\n\n" + "无标点" * 500 + "\n"))

    assert [len(passage.content) for passage in parsed.passages] == [600, 600, 300]
    assert [passage.source_id for passage in parsed.passages] == ["manual#全文#1", "manual#全文#2", "manual#全文#3"]


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
    """只证明最终排序稳定：两路名次固定以后，候选列表怎么排都得到同一个结果。"""
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
