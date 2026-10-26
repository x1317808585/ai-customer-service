from typing import Any

from pydantic import BaseModel


class StaleRecordError(Exception):
    """管理员要处理的记录状态已经变了（比如顾客已撤回、另一位管理员已关闭）。

    返回 409，并附带记录的当前状态，前端据此刷新页面。只在管理接口里使用，不影响用户接口的约定。
    """

    def __init__(self, detail: str, name: str, current: BaseModel) -> None:
        super().__init__(detail)
        self.body: dict[str, Any] = {"detail": detail, name: current.model_dump(mode="json")}
