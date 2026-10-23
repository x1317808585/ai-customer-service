from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select

from app.conversations import ConversationService
from app.database import SessionFactoryDep
from app.models import User

DEMO_USERNAME = "demo"


async def get_current_user(session_factory: SessionFactoryDep) -> User:
    """当前用户。登录功能完成前固定返回演示用户；第 7 步做登录时只改这个函数。"""
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.username == DEMO_USERNAME))
    if user is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "演示用户不存在，请先运行 uv run python -m scripts.create_demo_user",
        )
    return user


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


CurrentUserDep = Annotated[User, Depends(get_current_user)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
