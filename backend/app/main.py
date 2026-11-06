from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langchain_core.language_models import BaseChatModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.action_kinds import build_action_kinds
from app.actions import ActionNotConfirmableError, ActionNotFoundError, ActionService
from app.agent.llm import create_chat_model
from app.agent.mcp_toolbox import McpToolbox
from app.agent.reply_agent import ReplyAgent, Toolbox
from app.api.actions import router as actions_router
from app.api.admin import router as admin_router
from app.api.admin.errors import StaleRecordError
from app.api.assistant import router as assistant_router
from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.memories import router as memories_router
from app.api.trusted_origin import TrustedOriginMiddleware
from app.business.knowledge_documents import KnowledgeDocuments
from app.business.orders import Orders
from app.business.returns import Returns
from app.business.tickets import Tickets
from app.config import Settings, get_settings
from app.conversations import (
    ConversationBusyError,
    ConversationNotFoundError,
    ConversationService,
)
from app.database import SessionDep
from app.embeddings import load_embedder


def create_app(
    settings: Settings | None = None,
    chat_model: BaseChatModel | None = None,
    toolbox: Toolbox | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。测试可以传入自己的配置、模型和工具箱；不传时读取 .env，连接 MCP Server。"""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_async_engine(app_settings.database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        reply_agent = ReplyAgent(
            chat_model or create_chat_model(app_settings), toolbox or McpToolbox(app_settings)
        )
        orders = Orders(session_factory)
        returns = Returns(session_factory)
        tickets = Tickets(session_factory)
        action_service = ActionService(session_factory, build_action_kinds(orders, returns, tickets))
        # 混合检索模式下要给上传的文档算向量，在这里加载 BGE-M3（大约十几秒，多占约 2GB 内存）
        knowledge_documents = KnowledgeDocuments(session_factory, load_embedder(app_settings))
        # 上次退出时没做完的索引任务都标成失败，管理员可以重新索引。只适用于单个后端进程，见 fail_interrupted
        await knowledge_documents.fail_interrupted()

        app.state.session_factory = session_factory
        app.state.returns = returns
        app.state.tickets = tickets
        app.state.action_service = action_service
        app.state.conversation_service = ConversationService(session_factory, reply_agent, action_service)
        app.state.knowledge_documents = knowledge_documents
        yield
        await engine.dispose()

    app = FastAPI(title="AI 客服", lifespan=lifespan)
    app.state.settings = app_settings
    app.add_middleware(TrustedOriginMiddleware, trusted_origins=app_settings.frontend_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(auth_router)
    app.include_router(conversations_router)
    app.include_router(actions_router)
    app.include_router(admin_router)
    app.include_router(memories_router)
    app.include_router(assistant_router)

    @app.exception_handler(ConversationNotFoundError)
    async def conversation_not_found(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "会话不存在"}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(ConversationBusyError)
    async def conversation_busy(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "上一条回复还在生成中"}, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(ActionNotFoundError)
    async def action_not_found(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse({"detail": "操作不存在"}, status_code=status.HTTP_404_NOT_FOUND)

    @app.exception_handler(ActionNotConfirmableError)
    async def action_not_confirmable(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ActionNotConfirmableError)
        body = {"detail": str(error), "action": error.action.model_dump(mode="json")}
        return JSONResponse(body, status_code=status.HTTP_409_CONFLICT)

    @app.exception_handler(StaleRecordError)
    async def stale_record(_request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, StaleRecordError)
        return JSONResponse(error.body, status_code=status.HTTP_409_CONFLICT)

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
