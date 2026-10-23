import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from app.config import Settings

REQUEST_TIMEOUT_SECONDS = 60
MAX_RETRIES = 2


def create_chat_model(settings: Settings) -> BaseChatModel:
    """根据配置创建聊天模型。调用方只依赖 BaseChatModel，不关心具体是哪一家。"""
    if settings.llm_provider == "fake":
        return OfflineChatModel()
    return ChatOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=MAX_RETRIES,
    )


class OfflineChatModel(BaseChatModel):
    """离线聊天模型：不联网、不需要 Key，把用户最后一句话按固定格式逐字返回。"""

    chunk_delay_seconds: float = 0.03

    @property
    def _llm_type(self) -> str:
        return "offline"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        reply = AIMessage(content=_offline_reply(messages))
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for character in _offline_reply(messages):
            await asyncio.sleep(self.chunk_delay_seconds)
            yield ChatGenerationChunk(message=AIMessageChunk(content=character))


def _offline_reply(messages: list[BaseMessage]) -> str:
    question = next(
        (message.text for message in reversed(messages) if isinstance(message, HumanMessage)),
        "",
    )
    return f"（离线模式）收到你的消息：{question}"
