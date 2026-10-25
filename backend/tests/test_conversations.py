import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions import ActionService
from app.agent.reply_agent import ReplyAgent
from app.business.returns import Returns
from app.conversations import ConversationService
from app.models import User
from tests.agent.doubles import FakeToolbox, ScriptedChatModel, always, blocking_tool, calls_tools

async def test_closing_the_reply_stream_early_closes_the_agent_and_frees_the_conversation(
    session_factory: async_sessionmaker[AsyncSession], users: dict[str, User]
) -> None:
    """直接关闭回复的事件流（不是取消任务）时，也要马上断开 MCP、清除“正在回复”标记，不能等垃圾回收。"""
    started = asyncio.Event()
    toolbox = FakeToolbox([blocking_tool("list_my_orders", started)])
    model = ScriptedChatModel(respond=always(calls_tools(("list_my_orders", {}))))
    actions = ActionService(session_factory, Returns(session_factory))
    service = ConversationService(session_factory, ReplyAgent(model, toolbox), actions)
    alice_id = users["alice"].id
    conversation = await service.create_conversation(alice_id)

    events = await service.send_message(alice_id, conversation.id, "查订单")
    async for event in events:
        if event.type == "tool.status":
            break
    await asyncio.wait_for(started.wait(), 5)
    await events.aclose()

    assert (toolbox.opened, toolbox.closed) == (1, 1)
    again = await service.send_message(alice_id, conversation.id, "再问一次")  # 没有被当成“正在回复”
    await again.aclose()
