"""助手的运行方式。前端据此决定要不要显示“离线演示模式”的说明。不含任何用户数据，不需要登录。"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import SettingsDep

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class AssistantModeOut(BaseModel):
    # true：用内置的离线模型，回复按固定规则生成，不调用外部大模型 API
    offline: bool


@router.get("")
async def get_assistant_mode(settings: SettingsDep) -> AssistantModeOut:
    return AssistantModeOut(offline=settings.llm_provider == "fake")
