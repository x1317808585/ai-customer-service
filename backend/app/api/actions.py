from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.actions import ActionView
from app.api.dependencies import ActionServiceDep, CurrentUserDep

router = APIRouter(prefix="/api/actions", tags=["actions"])


class NoArguments(BaseModel):
    """确认、取消不接受任何参数：执行的内容只来自保存的待确认操作。

    带了多余的字段（比如商品、数量、金额）直接返回 422，方便及时发现前端用错了接口。
    """

    model_config = ConfigDict(extra="forbid")


@router.get("/{action_id}")
async def get_action(action_id: int, user: CurrentUserDep, actions: ActionServiceDep) -> ActionView:
    return await actions.get(user.id, action_id)


@router.post("/{action_id}/confirm")
async def confirm_action(
    action_id: int, user: CurrentUserDep, actions: ActionServiceDep, body: NoArguments | None = None
) -> ActionView:
    """执行成功或被业务规则拒绝都返回 200，结果看 status 是 executed 还是 failed。"""
    return await actions.confirm(user.id, action_id)


@router.post("/{action_id}/cancel")
async def cancel_action(
    action_id: int, user: CurrentUserDep, actions: ActionServiceDep, body: NoArguments | None = None
) -> ActionView:
    return await actions.cancel(user.id, action_id)
