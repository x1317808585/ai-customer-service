import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.types import CallToolResult, TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.returns import Returns
from app.business.tickets import Tickets
from app.config import Settings
from app.mcp_server.tools import SYSTEM_ERROR_MESSAGE
from app.models import Base, Order, User
from tests.mcp_server.conftest import OpenMcpSession

AddProduct = Callable[..., Awaitable[int]]
AddOrder = Callable[..., Awaitable[Order]]
SessionFactory = async_sessionmaker[AsyncSession]

READ_TOOLS = {
    "search_products",
    "get_product",
    "list_my_orders",
    "get_order",
    "list_my_returns",
    "list_my_tickets",
    "check_return_eligibility",
    "preview_return_request",
    "preview_return_cancellation",
    "preview_ticket",
}


def data_of(result: CallToolResult) -> Any:
    """取出工具成功时返回的结构化数据。返回列表的工具，数据放在 result 字段里。"""
    assert not result.isError, error_text(result)
    data = result.structuredContent
    assert data is not None
    return data.get("result", data)


def error_text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


async def give_customer_some_history(
    user: User, add_product: AddProduct, add_order: AddOrder, session_factory: SessionFactory
) -> dict[str, set[int]]:
    """给一个顾客准备订单、退货申请和工单，返回各自的 id，方便检查别人看不到。"""
    product = await add_product(f"{user.username} 买的耳机")
    order = await add_order(user.id, product, quantity=2, delivered_at=datetime.now(UTC) - timedelta(days=1))
    request = await Returns(session_factory).request_return(user.id, order.items[0].id, quantity=1, reason="坏了")
    ticket = await Tickets(session_factory).open_ticket(user.id, f"{user.username} 的问题", order_id=order.id)
    return {"orders": {order.id}, "returns": {request.id}, "tickets": {ticket.id}}


async def database_snapshot(session_factory: SessionFactory) -> dict[str, list[tuple[Any, ...]]]:
    """把每张表的所有行读出来，用来比较调用前后数据有没有变。"""
    snapshot: dict[str, list[tuple[Any, ...]]] = {}
    async with session_factory() as session:
        for table in Base.metadata.sorted_tables:
            rows = await session.execute(select(table).order_by(*table.primary_key))
            snapshot[table.name] = [tuple(row) for row in rows]
    return snapshot


async def test_all_tools_are_read_only_and_never_take_a_user_id(open_mcp_session: OpenMcpSession) -> None:
    async with open_mcp_session("alice") as session:
        tools = (await session.list_tools()).tools

    assert {tool.name for tool in tools} == READ_TOOLS
    for tool in tools:
        assert tool.annotations is not None and tool.annotations.readOnlyHint, tool.name
        assert "user" not in json.dumps(tool.inputSchema), f"{tool.name} 的参数里不能出现用户"


async def test_search_and_view_products(open_mcp_session: OpenMcpSession, add_product: AddProduct) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900, stock=3)
    await add_product("USB-C 快充线", price_cents=3900)

    async with open_mcp_session("alice") as session:
        found = data_of(await session.call_tool("search_products", {"keyword": "耳机"}))
        product = data_of(await session.call_tool("get_product", {"product_id": earbuds}))

    assert [item["name"] for item in found] == ["SoundPods 降噪耳机"]
    assert (product["price_cents"], product["stock"]) == (59900, 3)


async def test_each_user_only_sees_their_own_orders_returns_and_tickets(
    open_mcp_session: OpenMcpSession,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    owned = {
        name: await give_customer_some_history(users[name], add_product, add_order, session_factory)
        for name in ("alice", "bob")
    }

    async def ids_seen_by(username: str) -> dict[str, set[int]]:
        seen: dict[str, set[int]] = {"orders": set(), "returns": set(), "tickets": set()}
        async with open_mcp_session(username) as session:
            for _ in range(3):
                orders, returns, tickets = await asyncio.gather(
                    session.call_tool("list_my_orders", {}),
                    session.call_tool("list_my_returns", {}),
                    session.call_tool("list_my_tickets", {}),
                )
                seen["orders"] |= {order["id"] for order in data_of(orders)}
                seen["returns"] |= {request["id"] for request in data_of(returns)}
                seen["tickets"] |= {ticket["id"] for ticket in data_of(tickets)}
        return seen

    # alice 和 bob 同时反复查询，四类个人数据都不能串
    alice_seen, bob_seen = await asyncio.gather(ids_seen_by("alice"), ids_seen_by("bob"))

    assert alice_seen == owned["alice"]
    assert bob_seen == owned["bob"]


async def test_someone_elses_order_is_reported_as_not_found(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    alice_order = await add_order(users["alice"].id, earbuds, status="shipped")

    async with open_mcp_session("bob") as session:
        result = await session.call_tool("get_order", {"order_id": alice_order.id})

    assert result.isError
    assert f"订单 {alice_order.id} 不存在" in error_text(result)


async def test_extra_user_id_argument_cannot_switch_identity(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    alice_order = await add_order(users["alice"].id, earbuds, status="shipped")
    pretend_to_be_alice = {"user_id": users["alice"].id}

    async with open_mcp_session("bob") as session:
        single = await session.call_tool("get_order", {"order_id": alice_order.id, **pretend_to_be_alice})
        listed = await session.call_tool("list_my_orders", pretend_to_be_alice)

    assert single.isError and "不存在" in error_text(single)
    assert data_of(listed) == []


async def test_read_tools_do_not_change_any_data(
    open_mcp_session: OpenMcpSession,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    owned = await give_customer_some_history(users["alice"], add_product, add_order, session_factory)
    order_id = next(iter(owned["orders"]))
    return_id = next(iter(owned["returns"]))
    before = await database_snapshot(session_factory)

    async with open_mcp_session("alice") as session:
        products = data_of(await session.call_tool("search_products", {"keyword": "耳机"}))
        order = data_of(await session.call_tool("get_order", {"order_id": order_id}))
        item_id = order["items"][0]["id"]
        calls = {
            "get_product": {"product_id": products[0]["id"]},
            "list_my_orders": {},
            "list_my_returns": {},
            "list_my_tickets": {},
            "check_return_eligibility": {"order_item_id": item_id},
            "preview_return_request": {"order_item_id": item_id, "quantity": 1, "reason": "坏了"},
            "preview_return_cancellation": {"return_id": return_id},
            "preview_ticket": {"summary": "顾客要求人工处理", "order_id": order_id},
        }
        for name, arguments in calls.items():
            data_of(await session.call_tool(name, arguments))

    assert await database_snapshot(session_factory) == before


async def test_system_errors_are_hidden_from_the_model(
    open_mcp_session: OpenMcpSession, test_settings: Settings
) -> None:
    unreachable_database = test_settings.model_copy(update={"postgres_port": 1})

    async with open_mcp_session("alice", unreachable_database) as session:
        result = await session.call_tool("list_my_orders", {})

    assert result.isError
    assert SYSTEM_ERROR_MESSAGE in error_text(result)
    assert "Connect" not in error_text(result)


async def test_order_details_include_item_ids_amounts_and_times(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900)
    delivered_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    order = await add_order(users["alice"].id, earbuds, quantity=2, delivered_at=delivered_at)

    async with open_mcp_session("alice") as session:
        data = data_of(await session.call_tool("get_order", {"order_id": order.id}))

    assert data["items"][0]["id"] == order.items[0].id  # 以后申请退货要用订单项 id
    assert data["total_cents"] == 119800
    assert datetime.fromisoformat(data["delivered_at"]) == delivered_at


async def test_return_eligibility_and_preview_for_own_item(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机", price_cents=59900)
    order = await add_order(users["alice"].id, earbuds, quantity=2, delivered_at=datetime.now(UTC) - timedelta(days=1))
    item_id = order.items[0].id

    async with open_mcp_session("alice") as session:
        eligibility = data_of(await session.call_tool("check_return_eligibility", {"order_item_id": item_id}))
        preview = data_of(
            await session.call_tool(
                "preview_return_request", {"order_item_id": item_id, "quantity": 2, "reason": "左耳没声音"}
            )
        )

    assert (eligibility["eligible"], eligibility["returnable_quantity"]) == (True, 2)
    assert preview == {
        "order_id": order.id,
        "order_item_id": item_id,
        "product_name": "SoundPods 降噪耳机",
        "quantity": 2,
        "refund_cents": 119800,
        "reason": "左耳没声音",
    }


async def test_cannot_preview_a_return_for_someone_elses_item(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, delivered_at=datetime.now(UTC) - timedelta(days=1))

    async with open_mcp_session("bob") as session:
        result = await session.call_tool(
            "preview_return_request", {"order_item_id": order.items[0].id, "quantity": 1, "reason": "坏了"}
        )

    assert result.isError
    assert f"订单项 {order.items[0].id} 不存在" in error_text(result)


async def test_preview_cancelling_own_return_but_not_someone_elses(
    open_mcp_session: OpenMcpSession,
    users: dict[str, User],
    add_product: AddProduct,
    add_order: AddOrder,
    session_factory: SessionFactory,
) -> None:
    owned = await give_customer_some_history(users["alice"], add_product, add_order, session_factory)
    return_id = next(iter(owned["returns"]))

    async with open_mcp_session("alice") as session:
        preview = data_of(await session.call_tool("preview_return_cancellation", {"return_id": return_id}))
    async with open_mcp_session("bob") as session:
        refused = await session.call_tool("preview_return_cancellation", {"return_id": return_id})

    assert (preview["return_id"], preview["product_name"], preview["quantity"]) == (return_id, "alice 买的耳机", 1)
    assert refused.isError
    assert f"退货申请 {return_id} 不存在" in error_text(refused)


async def test_preview_ticket_with_and_without_an_order(
    open_mcp_session: OpenMcpSession, users: dict[str, User], add_product: AddProduct, add_order: AddOrder
) -> None:
    earbuds = await add_product("SoundPods 降噪耳机")
    order = await add_order(users["alice"].id, earbuds, status="shipped")

    async with open_mcp_session("alice") as session:
        about_order = data_of(
            await session.call_tool("preview_ticket", {"summary": " 顾客反馈物流三天没更新 ", "order_id": order.id})
        )
        general = data_of(await session.call_tool("preview_ticket", {"summary": "顾客想咨询企业采购"}))

    assert about_order == {"order_id": order.id, "summary": "顾客反馈物流三天没更新"}
    assert general == {"order_id": None, "summary": "顾客想咨询企业采购"}
