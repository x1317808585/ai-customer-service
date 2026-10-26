/**
 * 顾客端和管理端共用的部分：请求后端的方法、错误类型、登录和当前用户。
 *
 * 只有一端用到的接口放在 lib/customer/api.ts 或 lib/admin/api.ts。
 */

import type { ActionView } from "@/lib/actions";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL;

export type User = {
  id: number;
  username: string;
  role: "customer" | "admin";
};

/** 会话里的一条消息。顾客的聊天页和管理端查看的聊天记录都是这个格式。 */
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

export function jsonBody(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

/** 请求后端接口，自动带上登录 Cookie。非 2xx 时抛出 ApiError。 */
export async function request(path: string, init?: RequestInit): Promise<Response> {
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
