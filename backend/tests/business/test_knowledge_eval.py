"""知识库检索评测：用 backend/knowledge 下的真实文档，检查检索和拒答的效果。

题目分三类：
- 型号：问题里带型号或商品名，必须答对。
- 换说法：不用文档里的原词提问。允许拒答，但一旦回答就必须答对。
- 无答案：知识库里确实没有答案，包括“商品对了、但文档没写所问内容”的相似问题，必须拒答。

“答对”是指返回的段落只有能回答这道题的那一节。现在每道题能回答的小节只有 expected 这一节（对照文档逐题确认过），
第一段对、后面还跟着别的小节，记为“夹带”，任何类别都不能接受。以后加入要多个小节才能回答的题，
要给题目记下允许的来源集合，不能再按返回了几段来判定。

题目分三组，都不要为了让测试通过而修改题目：
- 调参组（tuning）：只用来调分词和阈值，τ（MIN_SIMILARITY）就是按它定的。
- 回归组（regression）：原来的验收组，见下面的第一次验收。
- 新验收组（acceptance）：修正同分规则之前就定好的 14 道新题，见下面的第二次验收。它也已经用过，不再是没见过的题。

两种检索模式：
- 关键词：默认就跑调参组和回归组。看逐题报告：uv run pytest tests/business/test_knowledge_eval.py -s
- 混合（关键词 + BGE-M3）：需要真实模型，默认不跑。每次只跑一组：
    uv run pytest tests/business/test_knowledge_eval.py -m embedding -k tuning -s
  回归组和新验收组还要逐题和关键词模式比较：关键词答对的题，混合模式也必须答对；
  关键词还有没答对的换说法题时，混合模式答对的换说法题必须更多。关键词本来就全对时，报告里写“没有观察到增益”。

验收记录（混合模式，τ = 0.67，只返回一段）：
- 第一次验收（当时的验收组，现在的回归组）：型号题“USB-C 快充线有多长？”答错，未达标。原因是两路名次互换、
  RRF 同分时按写入顺序排。改成 fused_order 的同分规则以后，这组题改作回归测试，混合模式下通过。
- 第二次验收（新验收组）：型号 4 题全对；换说法 2 对、2 拒、1 错；无答案 3 拒、2 错，未达标。原因：
  1. 向量相似度分不开“商品对了但文档没写”和“换了说法”：无答案题的最高相似度达到 0.734
     （“USB-C 快充线支持数据传输吗？”），而有答案的换说法题低到 0.660，任何 τ 都不能同时保住召回和拒答。
  2. 关键词覆盖率相同时，关键词名次本身由写入顺序决定，“关键词名次优先”的同分规则退化成按写入顺序
     （“退货申请提交了还能反悔吗？”）。
- 结论：14B 的基础设施已完成，混合检索没有通过阶段验收，M2 检索效果未达标。混合模式默认关闭，实际运行用关键词模式。
  新验收组在混合模式下的测试会失败，这是如实保留的记录。两组验收题都作为以后的回归材料，失败的题不删、不放宽。
  第 18 步接入重排后，还要再用没见过的题验收拒答和错误依据，不能预先认定加了重排就能达标。
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import (
    MIN_SIMILARITY,
    Candidate,
    Embedder,
    KnowledgeBase,
    Supported,
    read_documents,
)
from app.config import Settings
from app.embeddings import BgeM3Embedder

Category = Literal["型号", "换说法", "无答案"]


@dataclass(frozen=True)
class Case:
    category: Category
    question: str
    expected: str | None  # 能回答这个问题的小节（来源标识）；None 表示知识库里没有答案


TUNING_CASES = [
    Case("型号", "K8 是什么轴？", "keyboard-k8#轴体"),
    Case("型号", "SoundPods 的保修期多久？", "soundpods#保修"),
    Case("型号", "USB-C 快充线支持多少瓦快充？", "usb-c-cable#快充功率"),
    Case("型号", "65W 氮化镓充电器两个口同时用是多少瓦？", "charger-65w#输出功率"),
    Case("型号", "65W 氮化镓充电器有多大功率？", "charger-65w#输出功率"),
    Case("型号", "K8 怎么调背光亮度？", "keyboard-k8#背光"),
    Case("换说法", "买了一周了还能退吗？", "return-policy#退货期限"),
    Case("换说法", "刻了字的杯子能退吗？", "return-policy#不支持退货的商品"),
    Case("换说法", "耳机充一次电能用多久？", "soundpods#续航与充电"),
    Case("换说法", "键盘能用蓝牙连吗？", "keyboard-k8#连接方式"),
    Case("换说法", "下单以后多久能发出来？", "shipping#发货时间"),
    Case("无答案", "K8 能同时连接几台设备？", None),
    Case("无答案", "K8 怎么样？", None),
    Case("无答案", "SoundPods 防水吗？", None),
    Case("无答案", "能开发票吗？", None),
    Case("无答案", "有没有线下门店？", None),
    Case("无答案", "支持分期付款吗？", None),
]

REGRESSION_CASES = [
    Case("型号", "K8 的保修期是多久？", "keyboard-k8#保修"),
    Case("型号", "SoundPods 续航多久？", "soundpods#续航与充电"),
    Case("型号", "SoundPods 怎么切换降噪模式？", "soundpods#降噪"),
    Case("型号", "USB-C 快充线有多长？", "usb-c-cable#线身"),
    Case("型号", "K8 支持 Mac 吗？", "keyboard-k8#系统兼容"),
    Case("换说法", "收到货超过七天还能退货吗？", "return-policy#退货期限"),
    Case("换说法", "定制的东西可以退吗？", "return-policy#不支持退货的商品"),
    Case("换说法", "耳机坏了保修多长时间？", "soundpods#保修"),
    Case("换说法", "退款按什么价格算？", "return-policy#退款金额"),
    Case("换说法", "要运费吗？", "shipping#运费"),
    Case("无答案", "SoundPods 的蓝牙能传多远？", None),
    Case("无答案", "K8 有几种颜色？", None),
    Case("无答案", "快充线是什么颜色？", None),
    Case("无答案", "4K 显示器的刷新率是多少？", None),
    Case("无答案", "可以用优惠券吗？", None),
]

ACCEPTANCE_CASES = [
    Case("型号", "SoundPods 附赠几种尺寸的耳塞？", "soundpods#耳塞尺寸"),
    Case("型号", "65W 充电器有几个 USB-C 口？", "charger-65w#接口"),
    Case("型号", "USB-C 快充线能给 Lightning 接口的手机充电吗？", "usb-c-cable#适用设备"),
    Case("型号", "K8 的轴体坏了能保修吗？", "keyboard-k8#保修"),
    Case("换说法", "退货申请提交了还能反悔吗？", "return-policy#撤回退货申请"),
    Case("换说法", "谁来审核我的退货？", "return-policy#退货审核"),
    Case("换说法", "能寄到香港吗？", "shipping#配送范围"),
    Case("换说法", "包裹一直没动静怎么办？", "shipping#物流异常"),
    Case("换说法", "东西还没发出去，地址写错了能改吗？", "faq#下单后可以修改收货地址吗"),
    Case("无答案", "SoundPods 有几种颜色？", None),
    Case("无答案", "K8 键盘有多重？", None),
    Case("无答案", "USB-C 快充线支持数据传输吗？", None),
    Case("无答案", "65W 氮化镓充电器有没有折叠插脚？", None),
    Case("无答案", "可以货到付款吗？", None),
]

GROUPS = {
    "tuning": ("调参组", TUNING_CASES),
    "regression": ("回归组（原验收组）", REGRESSION_CASES),
    "acceptance": ("新验收组", ACCEPTANCE_CASES),
}
KEYWORD_GROUPS = ("tuning", "regression")  # 关键词模式默认跑的组；新验收组的关键词结果只作对照，显示在混合模式的报告里

Outcome = Literal["答对", "夹带", "拒答", "答错"]

# 每类题能接受的结果。“夹带”在哪一类都不能接受
ACCEPTABLE: dict[Category, set[Outcome]] = {
    "型号": {"答对"},
    "换说法": {"答对", "拒答"},
    "无答案": {"拒答"},
}

REPORTED_CANDIDATES = 5  # 报告里每道题列出前几个候选


@dataclass(frozen=True)
class Result:
    case: Case
    sources: list[str]  # 检索返回的小节，最相关的在前；拒答时为空
    candidates: list[Candidate]  # 融合排序后的全部候选，附带每一步的判断
    seconds: float  # 这一次检索（含算问题的向量）用了多久

    @property
    def outcome(self) -> Outcome:
        if not self.sources:
            return "拒答"
        if self.sources[0] != self.case.expected:
            return "答错"
        return "答对" if len(self.sources) == 1 else "夹带"

    @property
    def acceptable(self) -> bool:
        return self.outcome in ACCEPTABLE[self.case.category]


@pytest.fixture(scope="session")
def bge_m3() -> Embedder:
    """真实的 BGE-M3，整个测试过程只加载一次。.env 里没有配置模型目录时跳过。"""
    model_dir = Settings().embedding_model_dir
    if not model_dir.strip():
        pytest.skip("没有配置 EMBEDDING_MODEL_DIR，跳过真实向量模型的评测")
    return BgeM3Embedder(Path(model_dir))


@pytest.mark.parametrize("group", KEYWORD_GROUPS)
async def test_keyword_retrieval_meets_the_bar(session_factory: async_sessionmaker[AsyncSession], group: str) -> None:
    name, cases = GROUPS[group]
    knowledge = KnowledgeBase(session_factory)
    await knowledge.rebuild(read_documents())

    results = await run_cases(knowledge, cases)

    report = format_report(f"{name}，检索模式：关键词", results)
    print(report)
    assert all(result.acceptable for result in results), report


@pytest.mark.embedding
@pytest.mark.timeout(300)  # 第一次要加载模型，再给 27 个小节算向量
@pytest.mark.parametrize("group", GROUPS)
async def test_hybrid_retrieval_meets_the_bar(
    session_factory: async_sessionmaker[AsyncSession], bge_m3: Embedder, group: str
) -> None:
    name, cases = GROUPS[group]
    hybrid = KnowledgeBase(session_factory, bge_m3)
    await hybrid.rebuild(read_documents())

    # 同一版知识库上只用关键词检索的结果，用来逐题对比
    baseline = await run_cases(KnowledgeBase(session_factory), cases)
    results = await run_cases(hybrid, cases)

    title = f"{name}，检索模式：混合（关键词 + BGE-M3），τ = {MIN_SIMILARITY}"
    report = format_report(title, results, baseline)
    print(report)
    assert all(result.acceptable for result in results), report
    if group != "tuning":
        regressed = [
            result.case.question
            for result, keyword in zip(results, baseline, strict=True)
            if keyword.outcome == "答对" and result.outcome != "答对"
        ]
        assert not regressed, f"关键词答对、混合模式没答对：{regressed}\n{report}"
        keyword_correct, total = correct_rewordings(baseline), rewordings(cases)
        assert correct_rewordings(results) > keyword_correct or keyword_correct == total, report


async def run_cases(knowledge: KnowledgeBase, cases: list[Case]) -> list[Result]:
    results = []
    for case in cases:
        started = time.perf_counter()
        found = await knowledge.search(case.question)
        seconds = time.perf_counter() - started
        sources = [passage.source_id for passage in found.passages] if isinstance(found, Supported) else []
        results.append(Result(case, sources, await knowledge.candidates(case.question), seconds))
    return results


def rewordings(cases: list[Case]) -> int:
    return sum(case.category == "换说法" for case in cases)


def correct_rewordings(results: list[Result]) -> int:
    return sum(result.case.category == "换说法" and result.outcome == "答对" for result in results)


def gain_verdict(results: list[Result], baseline: list[Result]) -> str:
    """换说法题和关键词模式相比有没有增益。关键词本来就全对时，如实写没有观察到增益。"""
    keyword, hybrid, total = correct_rewordings(baseline), correct_rewordings(results), rewordings(
        [result.case for result in results]
    )
    counts = f"换说法答对：关键词 {keyword}/{total}，混合 {hybrid}/{total}"
    if keyword == total:
        return f"{counts}，关键词已经全对，没有观察到增益"
    return f"{counts}，{'有增益' if hybrid > keyword else '没有增益，未达标'}"


def format_report(title: str, results: list[Result], baseline: list[Result] | None = None) -> str:
    lines = ["", f"===== {title}"]
    for index, result in enumerate(results):
        mark = "" if result.acceptable else "  ✗ 不可接受"
        compare = f"  （只用关键词：{baseline[index].outcome}）" if baseline else ""
        answer = ", ".join(result.sources) or "（拒答）"
        lines.append(
            f"{result.outcome}  {result.case.category}  {result.case.question}  →  {answer}"
            f"{compare}  {result.seconds * 1000:.0f}ms{mark}"
        )
        lines.append(f"      应为：{result.case.expected or '（拒答）'}")
        lines.extend(f"      {format_candidate(candidate)}" for candidate in result.candidates[:REPORTED_CANDIDATES])
    for category in ACCEPTABLE:
        group = [result for result in results if result.case.category == category]
        summary = "，".join(
            f"{outcome} {sum(result.outcome == outcome for result in group)}" for outcome in get_args(Outcome)
        )
        lines.append(f"{category}：{len(group)} 题，{summary}")
    if baseline:
        lines.append(gain_verdict(results, baseline))
    seconds = [result.seconds for result in results]
    lines.append(f"检索耗时：最长 {max(seconds) * 1000:.0f}ms，平均 {sum(seconds) / len(seconds) * 1000:.0f}ms")
    return "\n".join(lines)


def format_candidate(candidate: Candidate) -> str:
    """一个候选的明细：是否算依据、两路名次、覆盖率和关键词判断、相似度、型号数字检查、RRF 分数。"""

    def rank(value: int | None) -> str:
        return "-" if value is None else f"#{value}"

    similarity = "-" if candidate.similarity is None else f"{candidate.similarity:.3f}"
    return (
        f"{'依据' if candidate.is_evidence else '    '}  {candidate.passage.source_id}  "
        f"关键词{rank(candidate.keyword_rank)} 覆盖{candidate.coverage:.2f}{'✓' if candidate.keyword_ok else '✗'}  "
        f"向量{rank(candidate.vector_rank)} 相似{similarity}  "
        f"型号数字{'✓' if candidate.exact_ok else '✗'}  RRF {candidate.rrf:.4f}"
    )
