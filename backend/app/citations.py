"""引用：回复里摘录了知识库的哪段原文，就保存哪段的快照。

摘录的格式是“「原文」[来源：来源标识]”。最终回复完成后逐个核对：标识必须是本轮检索给出的候选，
而且紧挨在标识前面的引号里正好是那段候选原文。两项都对才算引用，同一来源只算一次。
快照随回复一起保存，之后知识库重建了，老回复的引用也不变。
"""

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

_SOURCE_MARK = re.compile(r"\[来源：([^\]]+)\]")


class Citation(BaseModel):
    """回复引用的一段原文快照和它的出处。"""

    model_config = ConfigDict(frozen=True)

    source_id: str
    document_title: str
    section: str
    content: str


def quote(content: str, source_id: str) -> str:
    """按引用格式摘录一段原文。"""
    return f"「{content}」[来源：{source_id}]"


def cited(reply: str, candidates: Iterable[Citation]) -> list[Citation]:
    """回复里按格式摘录了哪些候选原文，按出现顺序返回，同一来源只返回一次。"""
    by_source = {candidate.source_id: candidate for candidate in candidates}
    found: list[Citation] = []
    for mark in _SOURCE_MARK.finditer(reply):
        candidate = by_source.get(mark.group(1))
        if (
            candidate is not None
            and candidate not in found
            and reply[: mark.start()].endswith(f"「{candidate.content}」")
        ):
            found.append(candidate)
    return found
