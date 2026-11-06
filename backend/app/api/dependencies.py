from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status

from app.actions import ActionService
from app.auth import InvalidTokenError, read_user_id
from app.business.knowledge_documents import KnowledgeDocuments
from app.business.returns import Returns
from app.business.tickets import Tickets
from app.config import Settings
from app.conversations import ConversationService
from app.database import SessionFactoryDep
from app.models import User
from app.preferences import Preferences

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


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """要求当前用户是管理员：没登录 401，不是管理员 403。

    角色每次请求都从数据库读取，撤销管理员身份后，下一次请求就会被拒绝。
    """
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def get_action_service(request: Request) -> ActionService:
    return request.app.state.action_service


def get_tickets(request: Request) -> Tickets:
    return request.app.state.tickets


def get_returns(request: Request) -> Returns:
    return request.app.state.returns


def get_knowledge_documents(request: Request) -> KnowledgeDocuments:
    return request.app.state.knowledge_documents


def get_preferences(session_factory: SessionFactoryDep) -> Preferences:
    return Preferences(session_factory)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
AdminDep = Annotated[User, Depends(require_admin)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ActionServiceDep = Annotated[ActionService, Depends(get_action_service)]
TicketsDep = Annotated[Tickets, Depends(get_tickets)]
ReturnsDep = Annotated[Returns, Depends(get_returns)]
KnowledgeDocumentsDep = Annotated[KnowledgeDocuments, Depends(get_knowledge_documents)]
PreferencesDep = Annotated[Preferences, Depends(get_preferences)]
