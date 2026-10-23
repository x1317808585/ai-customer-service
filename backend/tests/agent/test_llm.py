import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from app.agent.llm import create_chat_model
from app.config import Settings

LLM_SETTING_NAMES = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
TEST_API_KEY = "sk-test-secret"


@pytest.fixture(autouse=True)
def clear_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉本机的大模型环境变量，保证测试只看到下面显式传入的配置。"""
    for name in ("LLM_PROVIDER", *LLM_SETTING_NAMES):
        monkeypatch.delenv(name, raising=False)


def make_settings(**llm_settings: str) -> Settings:
    """创建一份不读取 .env 的配置，数据库字段随便填，测试不会真的连接。"""
    return Settings(
        _env_file=None,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test",
        **llm_settings,
    )


async def test_fake_provider_replies_without_any_llm_settings() -> None:
    model = create_chat_model(make_settings(llm_provider="fake"))

    reply = await model.ainvoke([HumanMessage("你好")])

    assert reply.text == "（离线模式）收到你的消息：你好"


@pytest.mark.parametrize(
    ("llm_settings", "missing_name"),
    [
        ({"llm_api_key": TEST_API_KEY, "llm_model": "test-model"}, "LLM_BASE_URL"),
        ({"llm_base_url": "https://llm.example.com/v1", "llm_model": "test-model"}, "LLM_API_KEY"),
        ({"llm_base_url": "https://llm.example.com/v1", "llm_api_key": TEST_API_KEY}, "LLM_MODEL"),
        ({"llm_base_url": "   ", "llm_api_key": TEST_API_KEY, "llm_model": "test-model"}, "LLM_BASE_URL"),
    ],
    ids=["missing-base-url", "missing-api-key", "missing-model", "blank-base-url"],
)
def test_openai_compatible_reports_exactly_the_missing_setting(
    llm_settings: dict[str, str], missing_name: str
) -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(llm_provider="openai_compatible", **llm_settings)

    message = str(error.value)
    assert missing_name in message
    for other_name in set(LLM_SETTING_NAMES) - {missing_name}:
        assert other_name not in message
    assert TEST_API_KEY not in message


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(llm_provider="deepseek")
