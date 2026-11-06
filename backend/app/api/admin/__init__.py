"""管理接口：/api/admin 下的所有接口都要求管理员身份。

权限检查挂在整个路由上（不是每个接口自己调用），以后新增管理接口也不会漏掉。
"""

from fastapi import APIRouter, Depends

from app.api.admin.knowledge import router as knowledge_router
from app.api.admin.returns import router as returns_router
from app.api.admin.tickets import router as tickets_router
from app.api.dependencies import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])
router.include_router(tickets_router)
router.include_router(returns_router)
router.include_router(knowledge_router)
