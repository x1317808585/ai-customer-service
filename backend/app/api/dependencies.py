from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status

from app.actions import ActionService
from app.auth import InvalidTokenError, read_user_id
from app.config import Settings
from app.conversations import ConversationService
from app.database import SessionFactoryDep
from app.models import User

ACCESS_TOKEN_COOKIE = "access_token"


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]


async def get_current_user(
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    access_token: Annotated[str | None, Cookie()] = None,
) -> User:
    """从 Cookie 里的令牌确定当前用户。没有登录、令牌无效或用户已不存在时返回 401。"""
    not_logged_in = HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录或登录已过期")
    if access_token is None:
        raise not_logged_in
    try:
        user_id = read_user_id(access_token, settings.jwt_secret.get_secret_value())
    except InvalidTokenError as error:
        raise not_logged_in from error

    async with session_factory() as session:
        user = await session.get(User, user_id)
    if user is None:
        raise not_logged_in
    return user




def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def get_action_service(request: Request) -> ActionService:
    return request.app.state.action_service


CurrentUserDep = Annotated[User, Depends(get_current_user)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ActionServiceDep = Annotated[ActionService, Depends(get_action_service)]
