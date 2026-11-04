import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.business.catalog import Catalog, ProductView
from app.business.errors import BusinessError
from app.business.orders import MAX_LINE_QUANTITY, MAX_ORDER_LINES, OrderLine, OrderPreview, Orders, OrderView
from app.business.returns import (
    ReturnCancellationPreview,
    ReturnEligibility,
    ReturnPreview,
    Returns,
    ReturnView,
)
from app.business.tickets import TicketPreview, Tickets, TicketView

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(readOnlyHint=True)
SYSTEM_ERROR_MESSAGE = "系统暂时出错，请稍后再试"


def current_user_id() -> int:
    """这次调用是替哪个用户做的。只来自已校验的令牌，不来自工具参数。"""
    token = get_access_token()
    if token is None or token.subject is None:
        raise RuntimeError("请求里没有已校验的用户身份")
    return int(token.subject)


def hide_system_errors[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """业务错误的消息原样告诉模型；其他错误只说"系统出错"，细节写进日志。"""

    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except BusinessError as error:
            raise ToolError(str(error)) from error
        except Exception as error:
            logger.exception("MCP 工具 %s 执行出错", tool.__name__)
            raise ToolError(SYSTEM_ERROR_MESSAGE) from error

    return wrapper


def register_read_tools(mcp: FastMCP, session_factory: async_sessionmaker[AsyncSession]) -> None:
    """注册只读工具（包括只演练、不提交的预览工具）。金额字段的单位都是分（59900 表示 599.00 元）。"""
    catalog = Catalog(session_factory)
    orders = Orders(session_factory)
    returns = Returns(session_factory)
    tickets = Tickets(session_factory)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def search_products(
        keyword: Annotated[str, Field(description="商品名称或描述里的关键词，比如“耳机”")],
    ) -> list[ProductView]:
        """按关键词搜索商品，最多返回 10 个。价格单位是分。"""
        return await catalog.search_products(keyword)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def get_product(product_id: Annotated[int, Field(description="商品 ID")]) -> ProductView:
        """查看一个商品的详情：价格（分）、库存、是否支持退货。"""
        return await catalog.get_product(product_id)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def list_my_orders() -> list[OrderView]:
        """列出当前顾客最近的订单（最新的在前），包含每个订单的商品和金额（分）。"""
        return await orders.list_orders(current_user_id())

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def get_order(order_id: Annotated[int, Field(description="订单号")]) -> OrderView:
        """查看当前顾客的一个订单：状态、签收时间、商品明细和金额（分）。"""
        return await orders.get_order(current_user_id(), order_id)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def preview_order(
        lines: Annotated[
            list[OrderLine],
            Field(
                description="要买的商品（product_id）和件数（quantity），每种商品一行，"
                f"最多 {MAX_ORDER_LINES} 种，每种最多 {MAX_LINE_QUANTITY} 件"
            ),
        ],
    ) -> OrderPreview:
        """按正式下单的规则报价，返回将要创建的订单：每种商品的数量、单价和总额（单位分）。

        只生成预览，不会下单。顾客在页面上确认后，系统才会创建模拟订单（不含支付）。
        """
        return await orders.preview_order(lines)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def list_my_returns() -> list[ReturnView]:
        """列出当前顾客的退货申请及处理状态。"""
        return await returns.list_returns(current_user_id())

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def list_my_tickets() -> list[TicketView]:
        """列出当前顾客提交过的转人工工单。"""
        return await tickets.list_tickets(current_user_id())

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def check_return_eligibility(
        order_item_id: Annotated[int, Field(description="订单项编号（订单详情 items 里的 id，不是订单号）")],
    ) -> ReturnEligibility:
        """查询当前顾客的某个订单项能不能退货：还能退几件、退货期截止时间、不能退的原因。"""
        return await returns.check_eligibility(current_user_id(), order_item_id)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def preview_return_request(
        order_item_id: Annotated[int, Field(description="订单项编号（订单详情 items 里的 id，不是订单号）")],
        quantity: Annotated[int, Field(description="退几件")],
        reason: Annotated[str, Field(description="退货原因，用顾客的话概括")],
    ) -> ReturnPreview:
        """按正式提交的规则检查一遍退货申请，返回将要提交的内容（含退款金额，单位分）。

        只生成预览，不会提交。顾客在页面上确认后，系统才会真正提交。
        """
        return await returns.preview_return(current_user_id(), order_item_id, quantity, reason)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def preview_return_cancellation(
        return_id: Annotated[int, Field(description="退货申请编号（list_my_returns 里的 id）")],
    ) -> ReturnCancellationPreview:
        """检查当前顾客的一条退货申请能不能撤回（只有处理中的可以），返回将要撤回的内容。

        只生成预览，不会撤回。顾客在页面上确认后，系统才会真正撤回。
        """
        return await returns.preview_cancellation(current_user_id(), return_id)

    @mcp.tool(annotations=READ_ONLY)
    @hide_system_errors
    async def preview_ticket(
        summary: Annotated[
            str, Field(description="交给人工客服的问题摘要：用第三人称写清顾客遇到的问题和诉求，不超过 1000 字")
        ],
        order_id: Annotated[int | None, Field(description="相关的订单号，没有就不填")] = None,
    ) -> TicketPreview:
        """检查转人工工单的内容，返回将要创建的工单。

        只生成预览，不会创建。顾客在页面上确认后，系统才会创建工单，交给人工客服处理。
        """
        return await tickets.preview_ticket(current_user_id(), summary, order_id)
