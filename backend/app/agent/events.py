"""Agent 生成回复时发出的事件。会话服务把它们转换成发给前端的 SSE 事件。"""

from dataclasses import dataclass
from typing import Literal

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


AgentEvent = TextDelta | DraftDiscarded | ToolStatus
