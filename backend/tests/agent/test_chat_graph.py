from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from app.agent.chat_graph import build_chat_graph
from app.agent.llm import OfflineChatModel

EXPECTED_REPLY = "（离线模式）收到你的消息：你好"


async def test_reply_streams_to_caller_in_multiple_chunks() -> None:
    graph = build_chat_graph(OfflineChatModel(chunk_delay_seconds=0))

    chunks = [
        chunk.text
        async for chunk, _metadata in graph.astream(
            {"messages": [HumanMessage("你好")]}, stream_mode="messages"
        )
        if isinstance(chunk, AIMessageChunk) and chunk.text
    ]

    assert len(chunks) > 1
    assert "".join(chunks) == EXPECTED_REPLY


async def test_conversation_ends_with_user_message_then_one_complete_reply() -> None:
    graph = build_chat_graph(OfflineChatModel(chunk_delay_seconds=0))

    result = await graph.ainvoke({"messages": [HumanMessage("你好")]})

    messages = result["messages"]
    assert len(messages) == 2
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].text == "你好"
    assert isinstance(messages[1], AIMessage)
    assert messages[1].text == EXPECTED_REPLY
