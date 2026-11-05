"""顾客查看和删除自己的长期偏好。只能操作当前登录用户自己的记录。"""

from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import CurrentUserDep, PreferencesDep
from app.preferences import MemoryView

router = APIRouter(prefix="/api/me/memories", tags=["memories"])


@router.get("")
async def list_memories(user: CurrentUserDep, preferences: PreferencesDep) -> list[MemoryView]:
    return await preferences.list_for(user.id)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: int, user: CurrentUserDep, preferences: PreferencesDep) -> None:
    """删除后，下一轮回复和新会话的上下文里都不再有这条偏好。不存在或不是自己的，一律 404。"""
    if not await preferences.delete(user.id, memory_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "偏好不存在")
