import { describe, expect, it } from "vitest";

import { applyReplyEvent, describeToolProgress, EMPTY_DRAFT, type ReplyDraft } from "@/lib/replyDraft";
import type { SseEvent } from "@/lib/sse";

const delta = (text: string): SseEvent => ({ event: "message.delta", data: JSON.stringify({ text }) });
const discard: SseEvent = { event: "message.discard", data: "{}" };
const toolStatus = (callId: string, tool: string, status: string): SseEvent => ({
  event: "tool.status",
  data: JSON.stringify({ call_id: callId, tool, status }),
});

function replay(...events: SseEvent[]): ReplyDraft {
  return events.reduce(applyReplyEvent, EMPTY_DRAFT);
}

describe("applyReplyEvent", () => {
  it("把正文片段依次拼起来", () => {
    expect(replay(delta("你"), delta("好")).text).toBe("你好");
  });

  it("收到 message.discard 时清空之前的正文，只保留之后的", () => {
    const draft = replay(delta("我先查一下"), discard, delta("你有 1 个订单"));

    expect(draft.text).toBe("你有 1 个订单");
  });

  it("同一个工具调用的进度原地更新，不同调用各占一行", () => {
    const draft = replay(
      toolStatus("call-1", "list_my_orders", "running"),
      toolStatus("call-2", "get_order", "running"),
      toolStatus("call-1", "list_my_orders", "succeeded"),
    );

    expect(draft.tools).toEqual([
      { callId: "call-1", tool: "list_my_orders", status: "succeeded" },
      { callId: "call-2", tool: "get_order", status: "running" },
    ]);
  });

  it("不认识的事件不改变回复", () => {
    const draft = replay(delta("你好"));

    expect(applyReplyEvent(draft, { event: "message.done", data: '{"message_id": 1}' })).toBe(draft);
  });
});

describe("describeToolProgress", () => {
  it("用中文描述工具进度", () => {
    expect(describeToolProgress({ callId: "1", tool: "list_my_orders", status: "running" })).toBe("正在查询订单…");
    expect(describeToolProgress({ callId: "1", tool: "get_order", status: "failed" })).toBe("查询订单详情失败");
    expect(describeToolProgress({ callId: "1", tool: "unknown_tool", status: "succeeded" })).toBe("已查询数据");
  });
});
