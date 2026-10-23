"""在终端里和 AI 客服对话，亲眼观察流式输出。

用法（在 backend 目录下）：uv run python -m scripts.chat_in_terminal
"""

import asyncio

from langchain_core.messages import AIMessageChunk, HumanMessage

from app.agent.chat_graph import build_chat_graph
from app.agent.llm import create_chat_model
from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    graph = build_chat_graph(create_chat_model(settings))
    print(f"当前模型：{settings.llm_provider}（输入 exit 退出）")

    while (question := input("\n你：").strip()) != "exit":
        print("客服：", end="", flush=True)
        async for chunk, _metadata in graph.astream(
            {"messages": [HumanMessage(question)]}, stream_mode="messages"
        ):
            if isinstance(chunk, AIMessageChunk):
                print(chunk.text, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(main())
