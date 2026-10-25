import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.agent.chat_graph import PROPOSAL_NOTE, TOOL_LIMIT_REPLY, AgentLimits
from app.agent.events import ActionProposed, AgentEvent, DraftDiscarded, TextDelta, ToolStatus
from app.agent.reply_agent import REPLY_TIMEOUT_MESSAGE, TOOLS_UNAVAILABLE_MESSAGE, ReplyAgent, ReplyFailedError
from tests.agent.doubles import (
    FakeToolbox,
    ScriptedChatModel,
    UnreachableToolbox,
    always,
    blocking_tool,
    business_error_tool,
    calls_tools,
    counting_tool,
    final_text,
    in_order,
    make_tool,
    preview_tool,
)
from tests.conftest import InMemoryMcpToolbox

QUESTION = [HumanMessage("我的订单到哪了？")]
ALICE_ID = 1


async def collect(agent: ReplyAgent, history: list[BaseMessage] = QUESTION) -> list[AgentEvent]:
    return [event async for event in agent.stream_reply(ALICE_ID, history)]


def tool_statuses(events: list[AgentEvent]) -> list[tuple[str, str, str]]:
    return [(e.call_id, e.tool, e.status) for e in events if isinstance(e, ToolStatus)]


# ---------- 正常流程 ----------


async def test_plain_answer_needs_no_tools() -> None:
    toolbox = FakeToolbox([])
    agent = ReplyAgent(ScriptedChatModel(respond=always(AIMessage("你好呀"))), toolbox)

    events = await collect(agent)

    assert all(isinstance(event, TextDelta) for event in events)
    assert final_text(events) == "你好呀"
    assert (toolbox.opened, toolbox.closed) == (1, 1)


async def test_tool_result_goes_back_to_model_with_matching_call_id() -> None:
    orders, runs = counting_tool("list_my_orders", '{"result": [{"id": 7}]}')
    model = ScriptedChatModel(
        respond=in_order(calls_tools(("list_my_orders", {}), text="我先查一下"), AIMessage("你有 1 个订单"))
    )
    agent = ReplyAgent(model, FakeToolbox([orders]))

    events = await collect(agent)

    assert model.tool_names == ()  # 原模型对象没被修改，绑定工具得到的是新对象
    assert len(runs) == 1
    tool_result = model.calls[1][-1]
    assert isinstance(tool_result, ToolMessage)
    assert (tool_result.tool_call_id, tool_result.text) == ("call-1", '{"result": [{"id": 7}]}')
    assert tool_statuses(events) == [("call-1", "list_my_orders", "running"), ("call-1", "list_my_orders", "succeeded")]
    assert DraftDiscarded() in events
    assert final_text(events) == "你有 1 个订单"


async def test_business_error_goes_back_to_model_to_explain() -> None:
    model = ScriptedChatModel(
        respond=in_order(calls_tools(("get_order", {"order_id": 99})), AIMessage("没有找到订单 99"))
    )
    agent = ReplyAgent(model, FakeToolbox([business_error_tool("get_order", "订单 99 不存在")]))

    events = await collect(agent)

    tool_result = model.calls[1][-1]
    assert isinstance(tool_result, ToolMessage)
    assert (tool_result.status, tool_result.text) == ("error", "订单 99 不存在")
    assert tool_statuses(events)[-1] == ("call-1", "get_order", "failed")
    assert final_text(events) == "没有找到订单 99"


async def test_model_can_correct_itself_after_a_failed_call() -> None:
    orders, runs = counting_tool("list_my_orders")
    model = ScriptedChatModel(
        respond=in_order(
            calls_tools(("cancel_order", {"order_id": 1})),
            calls_tools(("list_my_orders", {})),
            AIMessage("查到了"),
        )
    )
    agent = ReplyAgent(model, FakeToolbox([orders]))

    events = await collect(agent)

    refused = model.calls[1][-1]
    assert isinstance(refused, ToolMessage) and refused.status == "error"
    assert "没有名为 cancel_order 的工具" in refused.text
    assert len(runs) == 1
    assert [status[1] for status in tool_statuses(events)] == ["list_my_orders", "list_my_orders"]
    assert final_text(events) == "查到了"


# ---------- 上限 ----------


async def test_calls_beyond_the_per_round_limit_are_not_executed() -> None:
    orders, runs = counting_tool("list_my_orders")
    four_calls = calls_tools(*[("list_my_orders", {})] * 4)
    model = ScriptedChatModel(respond=in_order(four_calls, AIMessage("好的")))
    agent = ReplyAgent(model, FakeToolbox([orders]), AgentLimits(max_tool_calls_per_round=3))

    await collect(agent)

    results = model.calls[1][-4:]
    assert len(runs) == 3
    assert [message.status for message in results if isinstance(message, ToolMessage)] == [
        "success",
        "success",
        "success",
        "error",
    ]


async def test_stops_with_a_fixed_reply_after_max_tool_rounds() -> None:
    orders, runs = counting_tool("list_my_orders")
    model = ScriptedChatModel(respond=always(calls_tools(("list_my_orders", {}))))
    agent = ReplyAgent(model, FakeToolbox([orders]), AgentLimits(max_tool_rounds=2))

    events = await collect(agent)

    assert len(runs) == 2
    assert len(model.calls) == 3
    assert final_text(events) == TOOL_LIMIT_REPLY


# ---------- 失败 ----------


async def test_unreachable_tools_fail_the_reply_before_asking_the_model() -> None:
    model = ScriptedChatModel(respond=always(AIMessage("不该被调用")))
    agent = ReplyAgent(model, UnreachableToolbox())

    with pytest.raises(ReplyFailedError, match=TOOLS_UNAVAILABLE_MESSAGE):
        await collect(agent)
    assert model.calls == []


@pytest.mark.parametrize("kind", ["timeout", "broken"])
async def test_tool_failure_fails_the_reply_instead_of_letting_model_guess(kind: str) -> None:
    started = asyncio.Event()

    async def broken() -> str:
        raise ConnectionResetError("MCP 连接断了")

    tool = blocking_tool("list_my_orders", started) if kind == "timeout" else make_tool("list_my_orders", broken)
    model = ScriptedChatModel(respond=always(calls_tools(("list_my_orders", {}))))
    toolbox = FakeToolbox([tool])
    agent = ReplyAgent(model, toolbox, AgentLimits(tool_call_seconds=0.2))
    events: list[AgentEvent] = []

    with pytest.raises(ReplyFailedError, match=TOOLS_UNAVAILABLE_MESSAGE):
        async for event in agent.stream_reply(ALICE_ID, QUESTION):
            events.append(event)

    assert len(model.calls) == 1
    assert tool_statuses(events)[-1] == ("call-1", "list_my_orders", "failed")
    assert toolbox.closed == 1


async def test_whole_reply_has_a_time_limit() -> None:
    started = asyncio.Event()
    model = ScriptedChatModel(respond=always(calls_tools(("list_my_orders", {}))))
    agent = ReplyAgent(
        model,
        FakeToolbox([blocking_tool("list_my_orders", started)]),
        AgentLimits(tool_call_seconds=10, reply_seconds=0.2),
    )

    with pytest.raises(ReplyFailedError, match=REPLY_TIMEOUT_MESSAGE):
        await collect(agent)


async def test_model_error_keeps_its_own_type_through_real_mcp(mcp_toolbox: InMemoryMcpToolbox) -> None:
    def explode(_messages: list[Any]) -> AIMessage:
        raise RuntimeError("模型服务出错")

    agent = ReplyAgent(ScriptedChatModel(respond=explode), mcp_toolbox)

    with pytest.raises(RuntimeError, match="模型服务出错"):
        await collect(agent)


# ---------- 中途停止 ----------


async def test_stopping_midway_cancels_the_tool_and_closes_the_toolbox() -> None:
    started = asyncio.Event()
    toolbox = FakeToolbox([blocking_tool("list_my_orders", started)])
    model = ScriptedChatModel(respond=always(calls_tools(("list_my_orders", {}))))
    agent = ReplyAgent(model, toolbox, AgentLimits(tool_call_seconds=10))

    events = agent.stream_reply(ALICE_ID, QUESTION)
    first = await anext(events)
    await asyncio.wait_for(started.wait(), 5)
    await events.aclose()

    assert first == ToolStatus("call-1", "list_my_orders", "running")
    assert (toolbox.opened, toolbox.closed) == (1, 1)


# ---------- 发起待确认操作 ----------

PREVIEW = {
    "order_id": 5,
    "order_item_id": 9,
    "product_name": "SoundPods 降噪耳机",
    "quantity": 1,
    "refund_cents": 59900,
    "reason": "左耳没声音",
}
RETURN_ARGS = {"order_item_id": 9, "quantity": 1, "reason": "左耳没声音"}


def proposals(events: list[AgentEvent]) -> list[ActionProposed]:
    return [event for event in events if isinstance(event, ActionProposed)]


async def test_successful_preview_proposes_an_action_and_tells_model_it_is_not_submitted() -> None:
    tool, _received = preview_tool(PREVIEW)
    model = ScriptedChatModel(
        respond=in_order(calls_tools(("preview_return_request", RETURN_ARGS)), AIMessage("请在上方确认"))
    )
    agent = ReplyAgent(model, FakeToolbox([tool]))

    events = await collect(agent)

    assert proposals(events) == [ActionProposed("return_request", RETURN_ARGS, PREVIEW)]
    tool_result = model.calls[1][-1]
    assert isinstance(tool_result, ToolMessage)
    assert PROPOSAL_NOTE in tool_result.text


async def test_only_one_proposal_per_reply_even_when_requested_in_parallel() -> None:
    tool, received = preview_tool(PREVIEW)
    model = ScriptedChatModel(
        respond=in_order(
            calls_tools(("preview_return_request", RETURN_ARGS), ("preview_return_request", RETURN_ARGS)),
            calls_tools(("preview_return_request", RETURN_ARGS)),
            AIMessage("请确认"),
        )
    )
    agent = ReplyAgent(model, FakeToolbox([tool]))

    events = await collect(agent)

    assert len(proposals(events)) == 1
    assert len(received) == 1
    refused = [m for m in model.calls[1][-2:] + model.calls[2][-1:] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(refused) == 2
    assert all("已经发起过一项待确认的申请" in message.text for message in refused)


async def test_failed_preview_proposes_nothing() -> None:
    model = ScriptedChatModel(
        respond=in_order(calls_tools(("preview_return_request", RETURN_ARGS)), AIMessage("不能退"))
    )
    agent = ReplyAgent(model, FakeToolbox([business_error_tool("preview_return_request", "已超过签收后 7 天的退货期")]))

    events = await collect(agent)

    assert proposals(events) == []
    assert final_text(events) == "不能退"


@pytest.mark.parametrize(
    ("tool_name", "kind", "arguments"),
    [
        ("preview_return_request", "return_request", RETURN_ARGS),
        ("preview_return_cancellation", "return_cancellation", {"return_id": 3}),
        ("preview_ticket", "ticket", {"summary": "顾客反馈耳机左耳没声音，希望换货", "order_id": 5}),
    ],
)
async def test_each_preview_tool_proposes_its_own_kind(tool_name: str, kind: str, arguments: dict[str, Any]) -> None:
    preview = {"anything": "服务器算出的预览"}
    tool, received = preview_tool(preview, name=tool_name)
    model = ScriptedChatModel(respond=in_order(calls_tools((tool_name, arguments)), AIMessage("请在上方确认")))

    events = await collect(ReplyAgent(model, FakeToolbox([tool])))

    assert proposals(events) == [ActionProposed(kind, arguments, preview)]
    assert received == [arguments]


async def test_one_proposal_per_reply_across_different_kinds() -> None:
    return_tool, _ = preview_tool(PREVIEW)
    ticket_tool, ticket_received = preview_tool({"order_id": None, "summary": "要人工"}, name="preview_ticket")
    model = ScriptedChatModel(
        respond=in_order(
            calls_tools(("preview_return_request", RETURN_ARGS), ("preview_ticket", {"summary": "要人工"})),
            AIMessage("请确认"),
        )
    )

    events = await collect(ReplyAgent(model, FakeToolbox([return_tool, ticket_tool])))

    assert [proposal.kind for proposal in proposals(events)] == ["return_request"]
    assert ticket_received == []
