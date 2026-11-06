"""助手的运行方式：离线模型时告诉前端显示离线演示说明。不需要登录。"""

from typing import Any

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from tests.constants import ALLOWED_FRONTEND_ORIGIN


async def assistant_mode(settings: Settings) -> dict[str, Any]:
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)),
        base_url="http://test",
        headers={"Origin": ALLOWED_FRONTEND_ORIGIN},
    ) as client:
        response = await client.get("/api/assistant")
    assert response.status_code == 200
    return response.json()


async def test_offline_model_is_reported_without_logging_in(test_settings: Settings) -> None:
    assert await assistant_mode(test_settings) == {"offline": True}


async def test_real_model_is_not_offline(test_settings: Settings) -> None:
    settings = test_settings.model_copy(
        update={
            "llm_provider": "openai_compatible",
            "llm_base_url": "https://example.com/v1",
            "llm_api_key": SecretStr("test-key"),
            "llm_model": "test-model",
        }
    )

    assert await assistant_mode(settings) == {"offline": False}
