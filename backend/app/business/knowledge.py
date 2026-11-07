"""知识库检索：把 Markdown 文档切成原文段落，按问题检索，并判断依据够不够回答。

文档的保存和建索引在 knowledge_documents.py。检索只看启用并且已就绪的文档：签名检查、两路召回和词的权重
都用同一个过滤条件，并在同一个数据库快照里读取。

检索分两路，各取前 CANDIDATE_LIMIT 个候选：
- 关键词：用 Jieba 分词，PostgreSQL 全文检索（GIN 索引）找出含有问题里任意一个词的段落，
  再看问题里的词有多少出现在这一段里（越少见的词分量越重），即覆盖率。
- 向量（只在混合检索模式下）：用 BGE-M3 算出问题的向量，按余弦距离精确找出最接近的段落。

两路的名次用 RRF 融合排序（同分规则见 fused_order）。一个候选满足下面任意一条就算依据：
- 关键词判断通过：型号、数字原样出现；不是只靠文档标题命中；覆盖率达到 MIN_COVERAGE。
- 向量判断通过：相似度达到 MIN_SIMILARITY（τ），并且型号、数字原样出现。
问题里的型号、数字必须原样出现对两路都生效，比如问 K8 的问题不会拿 SoundPods 的段落来回答。
"""

import asyncio
import logging
import math
import re
from collections import Counter
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

import jieba
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, cast, func, null, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.models import KnowledgeChunk, KnowledgeDocument

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

# 一次最多返回几段原文。关键词判断还分不清同一说明书里的无关小节（会把它们一起带上），
# 所以先只返回最相关的一段；以后接入重排，再按评测结果决定是否放宽
MAX_PASSAGES = 1
CANDIDATE_LIMIT = 20  # 每一路先取出多少个候选段落，再逐个判断
MIN_COVERAGE = 0.6  # 问题里的词（按少见程度加权）至少有这么多出现在段落里，关键词判断才通过
# τ：只靠向量这一路成为依据时，和问题的余弦相似度（BGE-M3）至少要多少。只用评测的调参组来定：
# 调参组里无答案题的最高相似度是 0.609，换说法题正确小节的最低相似度是 0.725，取两者中间
MIN_SIMILARITY = 0.67
RRF_K = 60  # RRF 融合：每一路的第 n 名贡献 1 / (RRF_K + n) 分

MAX_NAME_CHARS = 80  # 文档名称（文件名去掉 .md）最多几个字
MAX_HEADING_CHARS = 100  # 文档标题、小节标题最多几个字
# 长小节切块的规则按字数算，不按 token：中英文、数字、代码的字数和 token 数之比各不相同，
# 所以这不等于设计文档里的“400–600 tokens 一块”。以后改成按 token 切分时，要重新评测检索效果
MAX_PART_CHARS = 600  # 不超过这么多字的小节不切，一个小节就是一段
OVERLAP_CHARS = 80  # 切开后，相邻两块最多重叠这么多字（整句重叠）

# 交给分词和向量模型的段落文本格式（见 passage_text）。改了格式就要改这个版本号，旧向量会被识别为对不上
PASSAGE_TEXT_VERSION = "title-section-content-v1"

# Jieba 词典里没有、又经常出现在商品说明里的词，不加的话会被切碎（比如“快充”切成“快”“充”）
DOMAIN_WORDS = ("usb-c", "快充", "快充线", "氮化镓", "茶轴", "热插拔", "背光", "降噪耳机")

DOMAIN_WORD_FREQUENCY = 100_000  # 给这些词很高的词频，和前后的字连在一起时也优先切出它们

# 问句里常见、本身不表达要问什么的词，不参与检索和判断
STOP_WORDS = frozenset(
    "的 了 吗 呢 吧 啊 呀 么 嘛 是 有 能 会 可以 可 要 想 请问 请 一下 我 你 您 我们 你们 它 这 那 这个 那个 "
    "什么 怎么 怎么样 怎样 如何 多 多少 多久 几 哪 哪些 哪里 和 与 及 或 在 就 都 也 还 后 前".split()
)

# “多长”问的是长度，只留下“长”去匹配（“长度”里有这个字）；其余同理
QUESTION_ATTRIBUTES = {"多长": "长", "多大": "大", "多远": "远", "多重": "重", "多高": "高", "多宽": "宽"}

_WORD_CHAR = re.compile(r"\w")
_EXACT_WORD = re.compile(r"[a-z0-9]")
_HAN = re.compile("[\u4e00-\u9fff]")
# 一句话：到句末标点或换行为止（含标点）；最后没有标点的部分也算一句
_SENTENCE = re.compile(r"[^。！？；!?;\n]*[。！？；!?;\n]|[^。！？；!?;\n]+")

# 能被检索的文档：启用并且已就绪。签名检查、两路召回、词的权重都用它
_SEARCHABLE = and_(KnowledgeDocument.enabled.is_(True), KnowledgeDocument.status == "ready")
_CHUNK_DOCUMENT = KnowledgeChunk.document_id == KnowledgeDocument.id


class KnowledgeFormatError(BusinessError):
    """知识文档的格式不对（包括段落超过向量模型的长度上限），说明哪里不对。"""


class TextTooLongError(BusinessError):
    """要编码的文本超过了向量模型的长度上限，不截断。index 是它在这一批文本里的位置。

    建索引时会转成指明段落来源的 KnowledgeFormatError；消息里只有 token 数，可以告诉顾客。
    """

    def __init__(self, index: int, tokens: int, limit: int) -> None:
        super().__init__(f"第 {index + 1} 条文本有 {tokens} 个 token，超过上限 {limit}")
        self.index = index
        self.tokens = tokens
        self.limit = limit


class QuestionTooLongError(BusinessError):
    """问题超过了向量模型的长度上限。不截断问题，让助手精简后再查。"""


class Embedder(Protocol):
    """把文本变成向量。真实实现在 app/embeddings.py（BGE-M3），测试里用假的。"""

    # 向量签名：签名不同的两个向量器算出来的向量，不能放在一起比较
    signature: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回归一化的向量，和 texts 一一对应。任何一条超过长度上限都不截断，直接抛出 TextTooLongError。"""
        ...


def embedding_signature(embedder: Embedder | None) -> str | None:
    """建索引和检索用来比对的签名：向量器的签名加上编码文本的格式。关键词模式下为 None。"""
    return None if embedder is None else f"{embedder.signature};text={PASSAGE_TEXT_VERSION}"


class Passage(BaseModel):
    """检索到的一段原文和它的出处。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    source_id: str  # “文件名#小节标题”，长小节切开后是“文件名#小节标题#序号”
    document_title: str
    section: str
    content: str


class Supported(BaseModel):
    """找到了充分的依据：通过判断的原文（目前只有最相关的一段，见 MAX_PASSAGES）。"""

    model_config = ConfigDict(frozen=True)

    passages: tuple[Passage, ...]


class Insufficient(BaseModel):
    """没有找到充分的依据，回复时应该直说，不能编。"""

    model_config = ConfigDict(frozen=True)


SearchResult = Supported | Insufficient


@dataclass(frozen=True)
class Candidate:
    """一个候选段落：它在两路召回里的名次和分数，以及依据判断的结果。检索和评测报告用的是同一份。"""

    passage: Passage
    keyword_rank: int | None  # 关键词这一路的名次（按覆盖率排，从 1 开始）；没被关键词召回时为 None
    coverage: float  # 问题里的词（按少见程度加权）有多少出现在这一段里
    keyword_ok: bool  # 关键词判断通过：型号数字原样出现、不只靠文档标题命中、覆盖率达标
    vector_rank: int | None  # 向量这一路的名次（从 1 开始）；关键词模式下或没被向量召回时为 None
    similarity: float | None  # 和问题的余弦相似度；关键词模式下为 None
    exact_ok: bool  # 问题里的型号、数字都原样出现在这一段（含文档标题）里
    rrf: float  # 两路名次融合后的分数，越大越靠前

    @property
    def vector_ok(self) -> bool:
        """向量判断通过：相似度达到 τ，并且型号、数字原样出现。"""
        return self.similarity is not None and self.similarity >= MIN_SIMILARITY and self.exact_ok

    @property
    def is_evidence(self) -> bool:
        return self.keyword_ok or self.vector_ok


def fused_order(candidates: Iterable[Candidate]) -> list[Candidate]:
    """融合后的最终顺序：RRF 降序 → 关键词名次升序（没有关键词名次的排在后面）→ 来源标识。

    两路名次互换时（比如 1、2 和 2、1）RRF 必然相同，这时关键词名次靠前的优先：按设计，关键词负责型号和术语。
    这是工程取舍，要靠评测验证；关键词名次本身也是覆盖率并列时按文档名称和段落位置排出来的。
    """
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.rrf,
            candidate.keyword_rank is None,
            candidate.keyword_rank or 0,
            candidate.passage.source_id,
        ),
    )


@dataclass(frozen=True)
class DocumentFile:
    """一篇知识文档的文件。name 是文件名（不含 .md），markdown 是全文。"""

    name: str
    markdown: str


@dataclass(frozen=True)
class ParsedDocument:
    """检查过格式的文档：标题和切好的原文段落（按在文档里的顺序）。"""

    title: str
    passages: list[Passage]


def read_documents(directory: Path = KNOWLEDGE_DIR) -> list[DocumentFile]:
    """读取目录下所有 .md 文件，按文件名排序。"""
    return [DocumentFile(path.stem, path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.md"))]


def parse_document(file: DocumentFile) -> ParsedDocument:
    """检查格式并切成原文段落。格式不对抛出 KnowledgeFormatError，说明哪里不对。

    按“# 文档标题”和“## 小节标题”切分；不超过 MAX_PART_CHARS 字的小节就是一段，超过的再按句子切块（见 _parts）。
    """
    name = file.name
    if not 1 <= len(name) <= MAX_NAME_CHARS or "#" in name or name != name.strip():
        raise KnowledgeFormatError(f"文档名称（文件名）要有 1～{MAX_NAME_CHARS} 个字，不能包含 #，前后不能有空格：{name}")
    title, sections = _sections(file)
    counts = Counter(heading for heading, _ in sections)
    duplicates = [heading for heading, count in counts.items() if count > 1]
    if duplicates:
        raise KnowledgeFormatError(f"{name}：小节标题重复：{'、'.join(duplicates)}")

    passages = []
    for heading, content in sections:
        parts = _parts(content)
        for number, part in enumerate(parts, start=1):
            source_id = f"{name}#{heading}" if len(parts) == 1 else f"{name}#{heading}#{number}"
            passages.append(Passage(source_id=source_id, document_title=title, section=heading, content=part))
    return ParsedDocument(title, passages)


def passage_text(passage: Passage) -> str:
    """交给分词和向量模型的段落文本：文档标题、小节标题和原文。格式变了要改 PASSAGE_TEXT_VERSION。"""
    return f"{passage.document_title}\n{passage.section}\n{passage.content}"


def document_terms(text: str) -> set[str]:
    """段落里用来被检索的词：分出来的词和小词，再加上每个汉字。

    这样问“什么轴”能找到写着“茶轴”的段落；反过来不成立，问题里的词不拆成单字，避免太宽泛。
    """
    terms = {term for term in _tokenizer().cut_for_search(text.lower()) if _is_term(term)}
    return terms | {char for term in terms for char in term if _HAN.match(char)}


class KnowledgeBase:
    """知识库检索。不传向量器是关键词模式；传了是混合模式，两路召回。只检索启用并且已就绪的文档。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder | None = None) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._signature = embedding_signature(embedder)

    async def check_index(self) -> None:
        """混合模式下确认能被检索的文档都是用当前模型和编码方式建的索引，对不上就抛出异常。

        关键词模式不依赖向量，不用检查。MCP Server 启动时调用；每次检索还会在同一个快照里再核对一次。
        停用的、还没就绪的文档不参与检查，它们的旧签名不会挡住正常检索。
        """
        if self._embedder is None:
            return
        async with self._session_factory() as session:
            await self._check_signatures(session)

    async def search(self, question: str) -> SearchResult:
        """检索问题的依据：按融合后的名次，取通过判断的前 MAX_PASSAGES 段。

        数据库出错、文档的向量和当前模型对不上时直接抛出异常，不会当作“没有依据”。
        """
        evidence = [candidate.passage for candidate in await self.candidates(question) if candidate.is_evidence]
        if not evidence:
            return Insufficient()
        return Supported(passages=tuple(evidence[:MAX_PASSAGES]))

    async def candidates(self, question: str) -> list[Candidate]:
        """问题的全部候选段落（两路召回的并集），按 fused_order 排好，附带每一步的判断。"""
        words = _question_words(question)
        if not words:
            return []
        query_vector = await self._embed_question(question)

        query = cast(_any_of(set(words) | {part for parts in words.values() for part in parts}), TSQUERY)
        similarity = (
            null() if query_vector is None else 1 - KnowledgeChunk.embedding.cosine_distance(query_vector)
        ).label("similarity")
        searchable_chunks = (
            select(
                KnowledgeChunk,
                KnowledgeDocument.name,
                func.tsvector_to_array(KnowledgeChunk.search_vector),
                similarity,
            )
            .join(KnowledgeDocument, _CHUNK_DOCUMENT)
            .where(_SEARCHABLE)
        )
        # 同分时按文档名称和段落位置排，不按数据库 ID：重新建索引会改变 ID，但不会改变这个顺序
        stable_order = (KnowledgeDocument.name, KnowledgeChunk.position)

        async with self._snapshot() as session:
            if self._embedder is not None:
                await self._check_signatures(session)
            keyword_rows = (
                await session.execute(
                    searchable_chunks.where(KnowledgeChunk.search_vector.op("@@")(query))
                    .order_by(func.ts_rank(KnowledgeChunk.search_vector, query).desc(), *stable_order)
                    .limit(CANDIDATE_LIMIT)
                )
            ).all()
            vector_rows = []
            if query_vector is not None:
                vector_rows = (
                    await session.execute(
                        searchable_chunks.order_by(
                            KnowledgeChunk.embedding.cosine_distance(query_vector), *stable_order
                        ).limit(CANDIDATE_LIMIT)
                    )
                ).all()
            weights = await _word_weights(session, set(words))

        exact_words = {word for word in words if _EXACT_WORD.search(word)}
        total = sum(weights.values())
        judged: dict[int, _Judged] = {}
        for chunk, document_name, terms, chunk_similarity in [*keyword_rows, *vector_rows]:
            matched = _matched(words, set(terms))
            coverage = sum(weights[word] * share for word, share in matched.items()) / total
            exact_ok = all(matched[word] == 1 for word in exact_words)
            # 只靠文档标题命中（比如只问了商品名），说明不了这一段回答了所问的内容
            own_ok = any(_matched(words, document_terms(f"{chunk.section}\n{chunk.content}")).values())
            keyword_ok = exact_ok and own_ok and coverage >= MIN_COVERAGE
            judged[chunk.id] = _Judged(chunk, document_name, coverage, exact_ok, keyword_ok, chunk_similarity)

        # 关键词这一路按覆盖率排名次，并列时按文档名称和段落位置；向量这一路按相似度（查询时已排好）
        keyword_ids = sorted(
            {row[0].id for row in keyword_rows},
            key=lambda chunk_id: (-judged[chunk_id].coverage, *judged[chunk_id].stable_key),
        )
        keyword_ranks = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids, start=1)}
        vector_ranks = {row[0].id: rank for rank, row in enumerate(vector_rows, start=1)}
        return fused_order(
            judged[chunk_id].candidate(keyword_ranks.get(chunk_id), vector_ranks.get(chunk_id)) for chunk_id in judged
        )

    @asynccontextmanager
    async def _snapshot(self) -> AsyncIterator[AsyncSession]:
        """一个 REPEATABLE READ 事务：签名检查、两路召回和词的权重读到的是同一版数据，停用或更新恰好发生在中间也不会混。"""
        async with self._session_factory() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            yield session

    async def _check_signatures(self, session: AsyncSession) -> None:
        mismatched = (
            await session.scalars(
                select(KnowledgeDocument.name)
                .where(_SEARCHABLE, KnowledgeDocument.embedding_signature.is_distinct_from(self._signature))
                .order_by(KnowledgeDocument.name)
            )
        ).all()
        if mismatched:
            raise RuntimeError(
                f"这些文档的索引和当前的向量模型（签名 {self._signature}）对不上：{'、'.join(mismatched)}。"
                "请在管理工作台重新索引这些文档"
            )

    async def _embed_question(self, question: str) -> list[float] | None:
        """混合模式下算出问题的向量；关键词模式下不算。问题超过长度上限时报业务错误，不截断。"""
        if self._embedder is None:
            return None
        try:
            # 编码是 CPU 密集的同步计算，放到线程里做，不阻塞事件循环
            [vector] = await asyncio.to_thread(self._embedder.embed, [question])
        except TextTooLongError as error:
            raise QuestionTooLongError(
                f"问题太长（{error.tokens} 个 token，上限 {error.limit}），请精简后再查"
            ) from error
        return vector


@dataclass(frozen=True)
class _Judged:
    """一个候选段落的判断结果，还没有融合名次。"""

    chunk: KnowledgeChunk
    document_name: str
    coverage: float
    exact_ok: bool
    keyword_ok: bool
    similarity: float | None

    @property
    def stable_key(self) -> tuple[str, int]:
        return (self.document_name, self.chunk.position)

    def candidate(self, keyword_rank: int | None, vector_rank: int | None) -> Candidate:
        ranks = [rank for rank in (keyword_rank, vector_rank) if rank is not None]
        return Candidate(
            passage=Passage.model_validate(self.chunk),
            keyword_rank=keyword_rank,
            coverage=self.coverage,
            keyword_ok=self.keyword_ok,
            vector_rank=vector_rank,
            similarity=self.similarity,
            exact_ok=self.exact_ok,
            rrf=sum(1 / (RRF_K + rank) for rank in ranks),
        )


def _sections(file: DocumentFile) -> tuple[str, list[tuple[str, str]]]:
    """按“# 文档标题”和“## 小节标题”切开，返回标题和（小节标题，正文）。

    标题之外、第一个小节之前不能有正文，小节不能为空；小节标题不能包含 #（来源标识用它分隔）。
    """
    name = file.name
    lines = file.markdown.splitlines()
    title_line = next((line for line in lines if line.strip()), "")
    if not title_line.startswith("# "):
        raise KnowledgeFormatError(f"{name}：第一行必须是“# 文档标题”")
    title = title_line[2:].strip()
    if not 1 <= len(title) <= MAX_HEADING_CHARS:
        raise KnowledgeFormatError(f"{name}：文档标题要有 1～{MAX_HEADING_CHARS} 个字")

    sections: list[tuple[str, list[str]]] = []
    for line in lines[lines.index(title_line) + 1 :]:
        if line.startswith("## "):
            heading = line[3:].strip()
            if not 1 <= len(heading) <= MAX_HEADING_CHARS or "#" in heading:
                raise KnowledgeFormatError(
                    f"{name}：小节标题要有 1～{MAX_HEADING_CHARS} 个字，不能包含 #：{heading[:MAX_HEADING_CHARS]}"
                )
            sections.append((heading, []))
        elif sections:
            sections[-1][1].append(line)
        elif line.strip():
            raise KnowledgeFormatError(f"{name}：正文必须写在“## 小节标题”下面")
    if not sections:
        raise KnowledgeFormatError(f"{name}：至少要有一个“## 小节标题”")

    result = []
    for heading, body in sections:
        content = "\n".join(body).strip()
        if not content:
            raise KnowledgeFormatError(f"{name}：小节“{heading}”没有内容")
        result.append((heading, content))
    return title, result


def _parts(content: str) -> list[str]:
    """不超过 MAX_PART_CHARS 字的小节不切。超过的按句子边界切成不超过 MAX_PART_CHARS 字的块，

    相邻两块重叠上一块结尾的几句（不超过 OVERLAP_CHARS 字）；一句话本身就超过上限时按上限硬切。
    """
    if len(content) <= MAX_PART_CHARS:
        return [content]
    sentences = [piece for sentence in _SENTENCE.findall(content) for piece in _hard_cut(sentence)]
    parts: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and sum(map(len, current)) + len(sentence) > MAX_PART_CHARS:
            parts.append("".join(current))
            current = _overlap(current, len(sentence))
        current.append(sentence)
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _overlap(previous: list[str], next_length: int) -> list[str]:
    """下一块开头要重叠的句子：取上一块结尾的几句，不超过 OVERLAP_CHARS 字，加上下一句也不超过 MAX_PART_CHARS 字。"""
    overlap: list[str] = []
    for sentence in reversed(previous):
        if sum(map(len, overlap)) + len(sentence) > OVERLAP_CHARS:
            break
        overlap.insert(0, sentence)
    if sum(map(len, overlap)) + next_length > MAX_PART_CHARS:
        return []
    return overlap


def _hard_cut(sentence: str) -> list[str]:
    return [sentence[start : start + MAX_PART_CHARS] for start in range(0, len(sentence), MAX_PART_CHARS)]


@cache
def _tokenizer() -> jieba.Tokenizer:
    """第一次用到时才加载 Jieba 词典（大约半秒），之后一直复用。"""
    jieba.setLogLevel(logging.WARNING)  # 不打印加载词典的调试信息
    tokenizer = jieba.Tokenizer()
    for word in DOMAIN_WORDS:
        tokenizer.add_word(word, freq=DOMAIN_WORD_FREQUENCY)
    return tokenizer


def _question_words(text: str) -> dict[str, frozenset[str]]:
    """问题里要问的词，以及每个词拆开后的小词（比如“保修期”→“保修”）。去掉标点和问句常用词。

    段落里没有“保修期”、但有“保修”时，也算覆盖了这个词（按小词出现了几成算）。
    """
    tokenizer = _tokenizer()
    words: dict[str, frozenset[str]] = {}
    for token in tokenizer.lcut(text.lower()):
        word = QUESTION_ATTRIBUTES.get(token, token)
        if _is_term(word):
            words[word] = frozenset(
                part for part in tokenizer.cut_for_search(word) if part != word and len(part) > 1 and _is_term(part)
            )
    return words


def _matched(words: dict[str, frozenset[str]], terms: set[str]) -> dict[str, float]:
    """问题里每个词被这些词覆盖了多少：词本身出现算 1；否则按它拆开后的小词出现了几成算。"""
    return {
        word: 1.0 if word in terms else (len(parts & terms) / len(parts) if parts else 0.0)
        for word, parts in words.items()
    }


def _is_term(token: str) -> bool:
    return bool(_WORD_CHAR.search(token)) and token not in STOP_WORDS


def _any_of(terms: set[str]) -> str:
    """含有任意一个词的全文检索条件。每个词都加引号，原样匹配，不再分词。"""
    quoted = ("'" + term.replace("\\", "\\\\").replace("'", "''") + "'" for term in sorted(terms))
    return " | ".join(quoted)


async def _word_weights(session: AsyncSession, words: set[str]) -> dict[str, float]:
    """每个词的分量：出现在越少段落里的词越重要，知识库里没有的词分量最大（BM25 的 IDF）。只统计能被检索的文档。"""
    lexemes = (
        select(func.unnest(func.tsvector_to_array(KnowledgeChunk.search_vector)).label("lexeme"))
        .select_from(KnowledgeChunk)
        .join(KnowledgeDocument, _CHUNK_DOCUMENT)
        .where(_SEARCHABLE)
        .subquery()
    )
    rows = await session.execute(
        select(lexemes.c.lexeme, func.count()).where(lexemes.c.lexeme.in_(words)).group_by(lexemes.c.lexeme)
    )
    document_counts: dict[str, int] = dict(rows.all())
    total = (
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, _CHUNK_DOCUMENT)
            .where(_SEARCHABLE)
        )
        or 0
    )
    return {
        word: math.log(1 + (total - document_counts.get(word, 0) + 0.5) / (document_counts.get(word, 0) + 0.5))
        for word in words
    }
