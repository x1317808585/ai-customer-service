import type { ActionView } from "@/lib/actions";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export type User = {
  id: number;
  username: string;
  role: "customer" | "admin";
};

export type Conversation = {
  id: number;
  title: string;
  created_at: string;
};

export type ChatMessage = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  /** 这条消息是确认卡片时，对应操作的当前状态 */
  action: ActionView | null;
};

/** 后端返回了非 2xx 状态码。data 是响应内容（比如 409 时附带的操作当前状态）。 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly data: unknown = null,
  ) {
    super(message);
  }
}

export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** 把任意错误转成给用户看的一句话。 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return "无法连接服务器，请确认后端已启动。";
  return error instanceof Error ? error.message : "发生未知错误，请刷新页面重试。";
}

export async function login(username: string, password: string): Promise<User> {
  return (await request("/api/auth/login", jsonBody("POST", { username, password }))).json();
}

export async function logout(): Promise<void> {
  await request("/api/auth/logout", { method: "POST" });
}

export async function getCurrentUser(): Promise<User> {
  return (await request("/api/auth/me")).json();
}

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

function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  if (!API_BASE_URL) {
    throw new Error("缺少 NEXT_PUBLIC_API_BASE_URL，请把 frontend/.env.example 复制为 .env.local");
  }
  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, credentials: "include" });
  if (!response.ok) {
    const data = await readJson(response);
    throw new ApiError(response.status, describeFailure(response.status, data), data);
  }
  return response;
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null; // 响应不是 JSON
  }
}

function describeFailure(status: number, data: unknown): string {
  if (typeof data === "object" && data !== null && "detail" in data && typeof data.detail === "string") {
    return data.detail;
  }
  if (status === 422) return "消息内容不符合要求，请检查后重试";
  return `请求失败（${status}）`;
}
