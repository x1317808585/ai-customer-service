import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.agent.llm import OfflineChatModel, create_chat_model
from app.config import Settings
from tests.constants import TEST_JWT_SECRET, TEST_MCP_TOKEN_SECRET

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
        jwt_secret=TEST_JWT_SECRET,
        mcp_token_secret=TEST_MCP_TOKEN_SECRET,
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


# ---------- 离线模型调用工具 ----------


def offline_model(*tool_names: str) -> OfflineChatModel:
    return OfflineChatModel(chunk_delay_seconds=0, tool_names=tool_names)


async def test_offline_model_calls_the_matching_query_tool_when_it_has_one() -> None:
    reply = await offline_model("list_my_orders").ainvoke([HumanMessage("我的订单到哪了")])

    assert [call["name"] for call in reply.tool_calls] == ["list_my_orders"]


async def test_offline_model_without_tools_just_echoes() -> None:
    reply = await offline_model().ainvoke([HumanMessage("我的订单到哪了")])

    assert reply.tool_calls == []
    assert reply.text == "（离线模式）收到你的消息：我的订单到哪了"


@pytest.mark.parametrize(
    ("structured", "status", "expected"),
    [
        ({"result": [{"id": 5}, {"id": 3}]}, "success", "（离线模式）查到 2 条订单（编号 5、3）。"),
        ({"result": []}, "success", "（离线模式）查到 0 条订单。"),
        (None, "error", "（离线模式）查询订单失败。"),
    ],
    ids=["two-orders", "no-orders", "failed"],
)
async def test_offline_model_summarizes_tool_results(
    structured: dict[str, object] | None, status: str, expected: str
) -> None:
    """MCP 工具的结构化结果放在 ToolMessage 的 artifact 里，正文是给模型看的文本块。"""
    result = ToolMessage(
        "（给模型看的文本）",
        tool_call_id="call-1",
        name="list_my_orders",
        status=status,
        artifact={"structured_content": structured} if structured is not None else None,
    )
    history = [
        HumanMessage("我的订单"),
        AIMessage("", tool_calls=[{"name": "list_my_orders", "args": {}, "id": "call-1"}]),
        result,
    ]

    reply = await offline_model("list_my_orders").ainvoke(history)

    assert reply.text == expected
