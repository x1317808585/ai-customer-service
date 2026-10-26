from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.admin.errors import StaleRecordError
from app.api.dependencies import AdminDep, ReturnsDep
from app.business.returns import InvalidReturnError, ReturnNotFoundError, ReturnNotPendingError, StaffReturnView
from app.models import ReturnStatus

router = APIRouter(prefix="/returns")


class CompleteReturnIn(BaseModel):
    """完成退货：备注可以不填。处理人取自当前登录的管理员，不接受别的字段。"""

    model_config = ConfigDict(extra="forbid")

    note: str | None = None


class RejectReturnIn(BaseModel):
    """拒绝退货：原因必须填写（顾客能看到）。"""

    model_config = ConfigDict(extra="forbid")

    reason: str


@router.get("")
async def list_returns(returns: ReturnsDep, status: ReturnStatus = "pending") -> list[StaffReturnView]:
    return await returns.list_for_staff(status)


@router.get("/{return_id}")
async def get_return(return_id: int, returns: ReturnsDep) -> StaffReturnView:
    try:
        return await returns.get_for_staff(return_id)
    except ReturnNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/{return_id}/complete")
async def complete_return(
    return_id: int, admin: AdminDep, returns: ReturnsDep, body: CompleteReturnIn | None = None
) -> StaffReturnView:
    """确认收到退货：状态变为已完成，商品放回库存。不代表退款已经到账（项目没有对接支付）。"""
    note = body.note if body else None
    try:
        return await returns.complete_return(admin.id, return_id, note)
    except ReturnNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except InvalidReturnError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except ReturnNotPendingError as error:
        raise StaleRecordError(str(error), "return", await returns.get_for_staff(return_id)) from error


@router.post("/{return_id}/reject")
async def reject_return(return_id: int, body: RejectReturnIn, admin: AdminDep, returns: ReturnsDep) -> StaffReturnView:
    try:
        return await returns.reject_return(admin.id, return_id, body.reason)
    except ReturnNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except InvalidReturnError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except ReturnNotPendingError as error:
        raise StaleRecordError(str(error), "return", await returns.get_for_staff(return_id)) from error
