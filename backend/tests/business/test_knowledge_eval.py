"""知识库检索评测：用 backend/knowledge 下的真实文档，检查检索和拒答的效果。

题目分三类：
- 型号：问题里带型号或商品名，必须答对（找到能回答的小节，并且排在第一）。
- 换说法：不用文档里的原词提问。现在只做关键词检索，允许拒答，但一旦回答就必须答对。
- 无答案：知识库里确实没有答案，包括“文档对了、但没写所问内容”的相似问题，必须拒答。

题目分两组：调参组用来调分词、阈值；验收组锁定不改，以后加向量检索时用同一组题对比效果。
不要为了让验收组通过而修改它的题目。看每道题的结果：uv run pytest tests/business/test_knowledge_eval.py -s
"""

from dataclasses import dataclass
from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.knowledge import KnowledgeBase, Supported, read_documents

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

ACCEPTANCE_CASES = [
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

Outcome = Literal["答对", "拒答", "答错"]

# 每类题能接受的结果
ACCEPTABLE: dict[Category, set[Outcome]] = {
    "型号": {"答对"},
    "换说法": {"答对", "拒答"},
    "无答案": {"拒答"},
}


@dataclass(frozen=True)
class Result:
    case: Case
    sources: list[str]  # 检索返回的小节，最相关的在前；拒答时为空

    @property
    def outcome(self) -> Outcome:
        if not self.sources:
            return "拒答"
        return "答对" if self.sources[0] == self.case.expected else "答错"

    @property
    def section_found(self) -> bool:
        """能回答的小节是否在返回的小节里（不要求排第一）。"""
        return self.case.expected in self.sources

    @property
    def decision_right(self) -> bool:
        """该答还是该拒，判断得对不对。"""
        return bool(self.sources) == (self.case.expected is not None)


@pytest.fixture
async def knowledge(session_factory: async_sessionmaker[AsyncSession]) -> KnowledgeBase:
    knowledge = KnowledgeBase(session_factory)
    await knowledge.rebuild(read_documents())
    return knowledge


@pytest.mark.parametrize("cases", [TUNING_CASES, ACCEPTANCE_CASES], ids=["调参组", "验收组"])
async def test_each_category_meets_its_bar(knowledge: KnowledgeBase, cases: list[Case]) -> None:
    results = []
    for case in cases:
        found = await knowledge.search(case.question)
        sources = [passage.source_id for passage in found.passages] if isinstance(found, Supported) else []
        results.append(Result(case, sources))

    report = format_report(results)
    print(report)
    unacceptable = [result for result in results if result.outcome not in ACCEPTABLE[result.case.category]]
    assert not unacceptable, report


def format_report(results: list[Result]) -> str:
    lines = ["", "结果  小节命中  判断正确  类别    问题  →  返回的小节"]
    for result in results:
        mark = "" if result.outcome in ACCEPTABLE[result.case.category] else "  ✗ 不可接受"
        lines.append(
            f"{result.outcome}  {'是' if result.section_found else '-'}  {'是' if result.decision_right else '否'}  "
            f"{result.case.category}  {result.case.question}  →  {', '.join(result.sources) or '（拒答）'}{mark}"
        )
    for category in ACCEPTABLE:
        group = [result for result in results if result.case.category == category]
        answered = sum(result.outcome == "答对" for result in group)
        refused = sum(result.outcome == "拒答" for result in group)
        lines.append(f"{category}：{len(group)} 题，答对 {answered}，拒答 {refused}，答错 {len(group) - answered - refused}")
    return "\n".join(lines)
