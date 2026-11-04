import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.agent.llm import OFFLINE_NO_EVIDENCE_REPLY, OfflineChatModel, create_chat_model
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


async def test_offline_model_previews_a_return_only_for_an_explicit_order_item() -> None:
    model = offline_model("preview_return_request", "list_my_returns")

    explicit = await model.ainvoke([HumanMessage("退货 订单项 3")])
    vague = await model.ainvoke([HumanMessage("我要退货")])

    assert [(call["name"], call["args"]) for call in explicit.tool_calls] == [
        ("preview_return_request", {"order_item_id": 3, "quantity": 1, "reason": "退货 订单项 3"})
    ]
    assert [call["name"] for call in vague.tool_calls] == ["list_my_returns"]


@pytest.mark.parametrize(
    ("question", "tool_name", "arguments"),
    [
        ("转人工 订单 5，耳机左边没声音", "preview_ticket", {"summary": "转人工 订单 5，耳机左边没声音", "order_id": 5}),
        ("我要转人工", "preview_ticket", {"summary": "我要转人工"}),
        ("撤回退货 3", "preview_return_cancellation", {"return_id": 3}),
        ("撤回退货申请 12", "preview_return_cancellation", {"return_id": 12}),
        ("下单商品 3", "preview_order", {"lines": [{"product_id": 3, "quantity": 1}]}),
        ("下单 商品 3，要 2 件", "preview_order", {"lines": [{"product_id": 3, "quantity": 2}]}),
    ],
    ids=["ticket-with-order", "ticket", "cancel-return", "cancel-return-long", "order", "order-quantity"],
)
async def test_offline_model_proposes_tickets_and_return_cancellations(
    question: str, tool_name: str, arguments: dict[str, object]
) -> None:
    model = offline_model(
        "preview_order", "preview_ticket", "preview_return_cancellation", "preview_return_request", "list_my_returns"
    )

    reply = await model.ainvoke([HumanMessage(question)])

    assert [(call["name"], call["args"]) for call in reply.tool_calls] == [(tool_name, arguments)]


@pytest.mark.parametrize(
    ("tool_name", "text", "status", "expected"),
    [
        ("preview_return_request", "（预览内容）", "success", "（离线模式）已生成退货申请，请在上方的卡片里确认。"),
        (
            "preview_return_request",
            "Error executing tool preview_return_request: 已超过签收后 7 天的退货期",
            "error",
            "（离线模式）无法申请退货：已超过签收后 7 天的退货期。",
        ),
        (
            "preview_return_cancellation",
            "（预览内容）",
            "success",
            "（离线模式）已生成撤回退货的申请，请在上方的卡片里确认。",
        ),
        (
            "preview_return_cancellation",
            "Error executing tool preview_return_cancellation: 退货申请已经撤回，不能再处理",
            "error",
            "（离线模式）无法撤回：退货申请已经撤回，不能再处理。",
        ),
        ("preview_ticket", "（预览内容）", "success", "（离线模式）已整理好转人工的问题，请在上方的卡片里确认。"),
    ],
    ids=["return-previewed", "return-refused", "cancel-previewed", "cancel-refused", "ticket-previewed"],
)
async def test_offline_model_explains_the_preview(tool_name: str, text: str, status: str, expected: str) -> None:
    history = [
        HumanMessage("（用户的话）"),
        AIMessage("", tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}]),
        ToolMessage(text, tool_call_id="call-1", name=tool_name, status=status),
    ]

    reply = await offline_model(tool_name).ainvoke(history)

    assert reply.text == expected


@pytest.mark.parametrize(
    ("tool_name", "records", "expected"),
    [
        (
            "list_my_tickets",
            [{"id": 2, "status": "open", "resolution": None}, {"id": 1, "status": "closed", "resolution": "已联系物流补发"}],
            "（离线模式）查到 2 条工单：2 待处理；1 已处理（处理结果：已联系物流补发）。",
        ),
        (
            "list_my_returns",
            [
                {"id": 3, "status": "pending", "resolution_note": None},
                {"id": 2, "status": "rejected", "resolution_note": "商品已使用"},
            ],
            "（离线模式）查到 2 条退货申请：3 处理中；2 已拒绝（说明：商品已使用）。",
        ),
        ("list_my_tickets", [], "（离线模式）查到 0 条工单。"),
    ],
    ids=["tickets", "returns", "no-tickets"],
)
async def test_offline_model_tells_status_and_result_of_tickets_and_returns(
    tool_name: str, records: list[dict[str, object]], expected: str
) -> None:
    history = [
        HumanMessage("我的工单和退货"),
        AIMessage("", tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}]),
        ToolMessage(
            "（给模型看的文本）",
            tool_call_id="call-1",
            name=tool_name,
            artifact={"structured_content": {"result": records}},
        ),
    ]

    reply = await offline_model(tool_name).ainvoke(history)

    assert reply.text == expected


# ---------- 离线模型查知识库 ----------

ALL_TOOLS = (
    "search_knowledge",
    "list_my_orders",
    "list_my_returns",
    "list_my_tickets",
    "search_products",
    "preview_order",
    "preview_ticket",
    "preview_return_cancellation",
    "preview_return_request",
)


@pytest.mark.parametrize(
    ("question", "tool_name"),
    [
        ("帮我退货，订单项 3", "preview_return_request"),
        ("退货政策是什么？", "search_knowledge"),
        ("我的退货申请处理到哪了？", "list_my_returns"),
        ("K8 是什么轴？", "search_knowledge"),
    ],
    ids=["proposal-first", "knowledge-words-before-records", "records", "everything-else"],
)
async def test_offline_model_routes_knowledge_questions(question: str, tool_name: str) -> None:
    reply = await offline_model(*ALL_TOOLS).ainvoke([HumanMessage(question)])

    assert [call["name"] for call in reply.tool_calls] == [tool_name]


def knowledge_result(evidence: dict[str, object]) -> list[object]:
    return [
        HumanMessage("退货政策是什么？"),
        AIMessage("", tool_calls=[{"name": "search_knowledge", "args": {}, "id": "call-1"}]),
        ToolMessage(
            "（给模型看的文本）",
            tool_call_id="call-1",
            name="search_knowledge",
            artifact={"structured_content": evidence},
        ),
    ]


async def test_offline_model_quotes_the_first_passage_with_its_source() -> None:
    passage = {
        "source_id": "return-policy#退货期限",
        "document_title": "退货政策",
        "section": "退货期限",
        "content": "签收后 7 天内可以申请退货。",
    }
    other = {**passage, "source_id": "return-policy#退款金额", "section": "退款金额", "content": "按成交单价退款。"}

    reply = await offline_model("search_knowledge").ainvoke(
        knowledge_result({"status": "supported", "passages": [passage, other]})
    )

    assert reply.text == (
        "（离线模式）根据《退货政策》“退货期限”：「签收后 7 天内可以申请退货。」[来源：return-policy#退货期限]。"
    )


async def test_offline_model_says_so_when_there_is_no_evidence() -> None:
    reply = await offline_model("search_knowledge").ainvoke(
        knowledge_result({"status": "insufficient", "passages": []})
    )

    assert reply.text == f"（离线模式）{OFFLINE_NO_EVIDENCE_REPLY}。"
