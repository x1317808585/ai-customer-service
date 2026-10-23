from datetime import timedelta

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import ACCESS_TOKEN_COOKIE, CurrentUserDep, SettingsDep
from app.auth import authenticate, create_access_token
from app.database import SessionDep
from app.models import UserRole

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: UserRole


@router.post("/login")
async def login(
    body: LoginIn, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    user = await authenticate(session, body.username, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    lifetime = timedelta(minutes=settings.access_token_minutes)
    token = create_access_token(user.id, settings.jwt_secret.get_secret_value(), lifetime)
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token,
        max_age=int(lifetime.total_seconds()),
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return UserOut.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: SettingsDep) -> None:
    response.delete_cookie(
        ACCESS_TOKEN_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )


@router.get("/me")
async def me(user: CurrentUserDep) -> UserOut:
    return UserOut.model_validate(user)
