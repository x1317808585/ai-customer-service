"""Agent 生成回复时发出的事件。会话服务把它们转换成发给前端的 SSE 事件。"""

from dataclasses import dataclass
from typing import Any, Literal

ToolCallStatus = Literal["running", "succeeded", "failed"]


@dataclass(frozen=True)
class TextDelta:
    """回答正文的一小段。"""

    text: str


@dataclass(frozen=True)
class DraftDiscarded:
    """模型决定先调用工具，之前输出的那段话（比如“我先查一下”）不算最终回答，丢弃。"""


@dataclass(frozen=True)
class ToolStatus:
    """一次工具调用的进度。只包含调用编号、工具名和状态，不包含参数和结果。"""

    call_id: str
    tool: str
    status: ToolCallStatus


@dataclass(frozen=True)
class ActionProposed:
    """模型通过预览工具发起了一项需要用户确认的操作。arguments 是执行参数，preview 是服务器算出的预览。"""

    kind: str
    arguments: dict[str, Any]
    preview: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeFound:
    """知识库检索找到了充分的依据。passages 是这些原文段落（含出处），本轮回复可以引用它们。"""

    passages: tuple[dict[str, Any], ...]


AgentEvent = TextDelta | DraftDiscarded | ToolStatus | ActionProposed | KnowledgeFound
