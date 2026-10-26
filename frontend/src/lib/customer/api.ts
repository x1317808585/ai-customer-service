/** 顾客端用到的接口：会话、消息、确认卡片。通用的请求方法和错误类型在 @/lib/api。 */

import type { ActionView } from "@/lib/actions";
import { jsonBody, request, type ChatMessage } from "@/lib/api";

export type Conversation = {
  id: number;
  title: string;
  created_at: string;
};

export async function listConversations(): Promise<Conversation[]> {
  return (await request("/api/conversations")).json();
}

export async function createConversation(): Promise<Conversation> {
  return (await request("/api/conversations", { method: "POST" })).json();
}

export async function listMessages(
  conversationId: number,
  signal?: AbortSignal,
): Promise<ChatMessage[]> {
  return (await request(`/api/conversations/${conversationId}/messages`, { signal })).json();
}

/** 发送消息，返回回复的 SSE 字节流。 */
export async function sendMessage(
  conversationId: number,
  content: string,
  signal: AbortSignal,
): Promise<ReadableStream<Uint8Array>> {
  const response = await request(`/api/conversations/${conversationId}/messages`, {
    ...jsonBody("POST", { content }),
    signal,
  });
  if (!response.body) throw new Error("响应没有内容");
  return response.body;
}

export async function getAction(actionId: number): Promise<ActionView> {
  return (await request(`/api/actions/${actionId}`)).json();
}

/** 确认并执行。返回 200 不代表已提交，要看返回的 status 是 executed 还是 failed。 */
export async function confirmAction(actionId: number): Promise<ActionView> {
  return (await request(`/api/actions/${actionId}/confirm`, { method: "POST" })).json();
}

export async function cancelAction(actionId: number): Promise<ActionView> {
  return (await request(`/api/actions/${actionId}/cancel`, { method: "POST" })).json();
}
