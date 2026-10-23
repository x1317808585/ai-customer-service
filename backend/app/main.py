from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.chat_graph import build_chat_graph
from app.agent.llm import create_chat_model
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.trusted_origin import TrustedOriginMiddleware
from app.config import Settings, get_settings
from app.conversations import (
    ConversationBusyError,
    ConversationNotFoundError,
    ConversationService,
)
from app.database import SessionDep


def create_app(
    settings: Settings | None = None, chat_model: BaseChatModel | None = None
) -> FastAPI:
    """创建 FastAPI 应用。测试可以传入自己的配置和模型；不传时读取 .env。"""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        chat_graph = build_chat_graph(chat_model or create_chat_model(app_settings))

        app.state.session_factory = session_factory
        app.state.conversation_service = ConversationService(session_factory, chat_graph)
        yield
        await engine.dispose()

    app = FastAPI(title="AI 客服", lifespan=lifespan)
    app.state.settings = app_settings
    app.add_middleware(TrustedOriginMiddleware, trusted_origins=app_settings.frontend_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(auth_router)
    app.include_router(conversations_router)

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "会话不存在"}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(ConversationBusyError)
    async def conversation_busy(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "上一条回复还在生成中"}, status_code=status.HTTP_409_CONFLICT)

    @app.get("/health")
    async def health(session: SessionDep) -> dict[str, str]:
        try:
            await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable"
            ) from error
        return {"status": "ok"}

    return app


app = create_app()
