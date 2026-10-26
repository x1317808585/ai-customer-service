import type { SseEvent } from "@/lib/customer/sse";

export type ToolCallStatus = "running" | "succeeded" | "failed";

export type ToolProgress = {
  callId: string;
  tool: string;
  status: ToolCallStatus;
};

/** 正在生成的回复：目前的正文，以及这次回复里各个工具调用的进度。 */
export type ReplyDraft = {
  text: string;
  tools: ToolProgress[];
};

export const EMPTY_DRAFT: ReplyDraft = { text: "", tools: [] };

/**
 * 根据一个 SSE 事件更新正在生成的回复。
 *
 * 规则和后端保存回复时一致：message.discard 表示模型决定先调用工具，之前的正文作废、清空重来。
 * 所以页面最后显示的正文，就是数据库里保存的那条回复。
 */
export function applyReplyEvent(draft: ReplyDraft, event: SseEvent): ReplyDraft {
  switch (event.event) {
    case "message.delta": {
      const { text } = JSON.parse(event.data) as { text: string };
      return { ...draft, text: draft.text + text };
    }
    case "message.discard":
      return { ...draft, text: "" };
    case "tool.status": {
      const data = JSON.parse(event.data) as { call_id: string; tool: string; status: ToolCallStatus };
      const progress: ToolProgress = { callId: data.call_id, tool: data.tool, status: data.status };
      const known = draft.tools.some((tool) => tool.callId === progress.callId);
      return {
        ...draft,
        tools: known
          ? draft.tools.map((tool) => (tool.callId === progress.callId ? progress : tool))
          : [...draft.tools, progress],
      };
    }
    default:
      return draft;
  }
}

const TOOL_LABELS: Record<string, string> = {
  search_products: "商品",
  get_product: "商品详情",
  list_my_orders: "订单",
  get_order: "订单详情",
  list_my_returns: "退货申请",
  list_my_tickets: "工单",
};

/** 把工具进度转成给用户看的一句话，比如“正在查询订单…”。 */
export function describeToolProgress(progress: ToolProgress): string {
  const label = TOOL_LABELS[progress.tool] ?? "数据";
  switch (progress.status) {
    case "running":
      return `正在查询${label}…`;
    case "succeeded":
      return `已查询${label}`;
    case "failed":
      return `查询${label}失败`;
  }
}
