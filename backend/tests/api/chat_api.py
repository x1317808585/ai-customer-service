"""API 测试共用的辅助函数：创建会话、发消息并解析 SSE、读取历史。"""

import json
from typing import Any

from httpx import AsyncClient

SseEvent = tuple[str, dict[str, Any]]


def parse_sse(body: str) -> list[SseEvent]:
    """把 SSE 响应体解析成 [(事件名, 数据), ...]。"""
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def create_conversation(client: AsyncClient) -> int:
    response = await client.post("/api/conversations")
    assert response.status_code == 201
    return response.json()["id"]


async def send_message(client: AsyncClient, conversation_id: int, content: str) -> list[SseEvent]:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": content}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


async def get_history(client: AsyncClient, conversation_id: int) -> list[dict[str, Any]]:
    response = await client.get(f"/api/conversations/{conversation_id}/messages")
    assert response.status_code == 200
    return response.json()


def displayed_text(events: list[SseEvent]) -> str:
    """按前端的规则拼出最终显示的回答：收到 message.discard 就清空重来。"""
    text = ""
    for name, data in events:
        if name == "message.discard":
            text = ""
        elif name == "message.delta":
            text += data["text"]
    return text


async def get_title(client: AsyncClient, conversation_id: int) -> str:
    conversations = (await client.get("/api/conversations")).json()
    return next(c["title"] for c in conversations if c["id"] == conversation_id)
