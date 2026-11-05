"""知识库：把说明文档按小节存进数据库，按问题检索，并判断依据够不够回答。

检索目前只用关键词：用 Jieba 分词，PostgreSQL 全文检索（GIN 索引）找出含有问题里任意一个词的小节，
再看问题里的词有多少出现在这一节里（越少见的词分量越重），以此判断依据是否充分。
问题里的型号、数字这类词必须原样出现，比如问 K8 的问题不会拿 SoundPods 的小节来回答。

混合检索模式下，重建时还会用向量器给每个小节算向量，并在 knowledge_index 里记下向量签名。
"""

import asyncio
import logging
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal, Protocol

import jieba
from pydantic import BaseModel, ConfigDict
from sqlalchemy import cast, delete, func, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.errors import BusinessError
from app.models import KnowledgeChunk, KnowledgeIndex

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"

MAX_PASSAGES = 3  # 一次最多返回几段原文
CANDIDATE_LIMIT = 20  # 全文检索先取出多少个候选小节，再逐个判断
MIN_COVERAGE = 0.6  # 问题里的词（按少见程度加权）至少有这么多出现在小节里，才算依据充分

# 交给分词和向量模型的小节文本格式（见 _passage_text）。改了格式就要改这个版本号，旧向量会被识别为对不上
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


class KnowledgeFormatError(BusinessError):
    """知识文档的格式不对（包括小节超过向量模型的长度上限），整个知识库不会重建。"""


class TextTooLongError(BusinessError):
    """要编码的文本超过了向量模型的长度上限，不截断。index 是它在这一批文本里的位置。

    重建时会转成指明小节来源的 KnowledgeFormatError；消息里只有 token 数，可以告诉顾客。
    """

    def __init__(self, index: int, tokens: int, limit: int) -> None:
        super().__init__(f"第 {index + 1} 条文本有 {tokens} 个 token，超过上限 {limit}")
        self.index = index
        self.tokens = tokens
        self.limit = limit


class Embedder(Protocol):
    """把文本变成向量。真实实现在 app/embeddings.py（BGE-M3），测试里用假的。"""

    # 向量签名：签名不同的两个向量器算出来的向量，不能放在一起比较
    signature: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """返回归一化的向量，和 texts 一一对应。任何一条超过长度上限都不截断，直接抛出 TextTooLongError。"""
        ...


class Passage(BaseModel):
    """检索到的一段原文和它的出处。"""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    source_id: str  # “文件名#小节标题”，重建知识库后不变
    document_title: str
    section: str
    content: str


class Supported(BaseModel):
    """找到了充分的依据：1～3 段原文，每段都通过了判断，最相关的在前。"""

    model_config = ConfigDict(frozen=True)

    passages: tuple[Passage, ...]


class Insufficient(BaseModel):
    """没有找到充分的依据，回复时应该直说，不能编。"""

    model_config = ConfigDict(frozen=True)


SearchResult = Supported | Insufficient


@dataclass(frozen=True)
class KnowledgeDocument:
    """一篇知识文档。name 是文件名（不含 .md），markdown 是全文。"""

    name: str
    markdown: str


def read_documents(directory: Path = KNOWLEDGE_DIR) -> list[KnowledgeDocument]:
    """读取目录下所有 .md 文件，按文件名排序。"""
    return [
        KnowledgeDocument(path.stem, path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.md"))
    ]


class KnowledgeBase:
    """知识库：重建、检索。不传向量器是关键词模式；传了是混合模式，重建时给每个小节算向量。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], embedder: Embedder | None = None) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._mode: Literal["keyword", "hybrid"] = "keyword" if embedder is None else "hybrid"
        self._signature = None if embedder is None else f"{embedder.signature};text={PASSAGE_TEXT_VERSION}"

    async def rebuild(self, documents: Sequence[KnowledgeDocument]) -> int:
        """用这些文档整体替换知识库，返回小节数。

        向量在事务外先全部算好：任何一篇格式不对、任何一节超过向量模型的长度上限，都不会改动数据库。
        小节和 knowledge_index 在同一个事务里替换，检索时不会看到新旧混在一起的知识库。
        """
        passages = [passage for document in documents for passage in _split(document)]
        counts = Counter(passage.source_id for passage in passages)
        duplicates = [source_id for source_id, count in counts.items() if count > 1]
        if duplicates:
            raise KnowledgeFormatError(f"来源标识重复：{'、'.join(duplicates)}")

        texts = [_passage_text(passage) for passage in passages]
        embeddings = await self._embed_passages(passages, texts)

        async with self._session_factory.begin() as session:
            await session.execute(delete(KnowledgeChunk))
            await session.execute(delete(KnowledgeIndex))
            for passage, text, embedding in zip(passages, texts, embeddings, strict=True):
                session.add(
                    KnowledgeChunk(
                        **passage.model_dump(),
                        search_vector=func.array_to_tsvector(sorted(_document_terms(text))),
                        embedding=embedding,
                    )
                )
            session.add(KnowledgeIndex(id=1, mode=self._mode, embedding_signature=self._signature))
        return len(passages)

    async def search(self, question: str) -> SearchResult:
        """检索问题的依据。数据库出错时直接抛出异常，不会当作“没有依据”。"""
        words = _question_words(question)
        if not words:
            return Insufficient()

        query = cast(_any_of(set(words) | {part for parts in words.values() for part in parts}), TSQUERY)
        async with self._session_factory() as session:
            candidates = (
                await session.execute(
                    select(KnowledgeChunk, func.tsvector_to_array(KnowledgeChunk.search_vector))
                    .where(KnowledgeChunk.search_vector.op("@@")(query))
                    .order_by(func.ts_rank(KnowledgeChunk.search_vector, query).desc(), KnowledgeChunk.id)
                    .limit(CANDIDATE_LIMIT)
                )
            ).all()
            if not candidates:
                return Insufficient()
            weights = await _word_weights(session, set(words))

        exact_words = {word for word in words if _EXACT_WORD.search(word)}
        total = sum(weights.values())
        scored = []
        for chunk, chunk_terms in candidates:
            matched = _matched(words, set(chunk_terms))
            coverage = sum(weights[word] * share for word, share in matched.items()) / total
            exact_ok = all(matched[word] == 1 for word in exact_words)
            # 只靠文档标题命中（比如只问了商品名），说明不了这一节回答了所问的内容
            own_ok = any(_matched(words, _document_terms(f"{chunk.section}\n{chunk.content}")).values())
            if exact_ok and own_ok and coverage >= MIN_COVERAGE:
                scored.append((coverage, chunk))
        if not scored:
            return Insufficient()

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return Supported(passages=tuple(Passage.model_validate(chunk) for _, chunk in scored[:MAX_PASSAGES]))

    async def _embed_passages(self, passages: list[Passage], texts: list[str]) -> list[list[float] | None]:
        """关键词模式下不算向量；混合模式下一次算完全部小节，超长的小节报出来源。"""
        if self._embedder is None:
            return [None] * len(passages)
        try:
            # 编码是 CPU 密集的同步计算，放到线程里做，不阻塞事件循环
            return await asyncio.to_thread(self._embedder.embed, texts)
        except TextTooLongError as error:
            raise KnowledgeFormatError(
                f"{passages[error.index].source_id}：有 {error.tokens} 个 token，"
                f"超过向量模型的上限 {error.limit}，请拆成更短的小节"
            ) from error


def _passage_text(passage: Passage) -> str:
    """交给分词和向量模型的小节文本：文档标题、小节标题和原文。格式变了要改 PASSAGE_TEXT_VERSION。"""
    return f"{passage.document_title}\n{passage.section}\n{passage.content}"


def _split(document: KnowledgeDocument) -> list[Passage]:
    """按“# 文档标题”和“## 小节标题”切分。标题之外、第一个小节之前不能有正文，小节不能为空。"""
    lines = document.markdown.splitlines()
    title_line = next((line for line in lines if line.strip()), "")
    if not title_line.startswith("# "):
        raise KnowledgeFormatError(f"{document.name}：第一行必须是“# 文档标题”")
    title = title_line[2:].strip()

    sections: list[tuple[str, list[str]]] = []
    for line in lines[lines.index(title_line) + 1 :]:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        elif line.strip():
            raise KnowledgeFormatError(f"{document.name}：正文必须写在“## 小节标题”下面")
    if not sections:
        raise KnowledgeFormatError(f"{document.name}：至少要有一个“## 小节标题”")

    passages = []
    for section, body in sections:
        content = "\n".join(body).strip()
        if not content:
            raise KnowledgeFormatError(f"{document.name}：小节“{section}”没有内容")
        passages.append(
            Passage(source_id=f"{document.name}#{section}", document_title=title, section=section, content=content)
        )
    return passages


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

    小节里没有“保修期”、但有“保修”时，也算覆盖了这个词（按小词出现了几成算）。
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


def _document_terms(text: str) -> set[str]:
    """小节里用来被检索的词：分出来的词和小词，再加上每个汉字。

    这样问“什么轴”能找到写着“茶轴”的小节；反过来不成立，问题里的词不拆成单字，避免太宽泛。
    """
    terms = {term for term in _tokenizer().cut_for_search(text.lower()) if _is_term(term)}
    return terms | {char for term in terms for char in term if _HAN.match(char)}


def _is_term(token: str) -> bool:
    return bool(_WORD_CHAR.search(token)) and token not in STOP_WORDS


def _any_of(terms: set[str]) -> str:
    """含有任意一个词的全文检索条件。每个词都加引号，原样匹配，不再分词。"""
    quoted = ("'" + term.replace("\\", "\\\\").replace("'", "''") + "'" for term in sorted(terms))
    return " | ".join(quoted)


async def _word_weights(session: AsyncSession, words: set[str]) -> dict[str, float]:
    """每个词的分量：出现在越少小节里的词越重要，知识库里没有的词分量最大（BM25 的 IDF）。"""
    lexemes = select(func.unnest(func.tsvector_to_array(KnowledgeChunk.search_vector)).label("lexeme")).subquery()
    rows = await session.execute(
        select(lexemes.c.lexeme, func.count()).where(lexemes.c.lexeme.in_(words)).group_by(lexemes.c.lexeme)
    )
    document_counts: dict[str, int] = dict(rows.all())
    total = await session.scalar(select(func.count()).select_from(KnowledgeChunk)) or 0
    return {
        word: math.log(1 + (total - document_counts.get(word, 0) + 0.5) / (document_counts.get(word, 0) + 0.5))
        for word in words
    }
