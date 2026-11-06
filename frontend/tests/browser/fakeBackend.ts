/**
 * 浏览器测试用的模拟后端：在浏览器里拦截前端发给后端的请求，用内存里的数据回应。
 *
 * 默认行为按真实接口的约定实现（401 / 403 / 404 / 409 / 422）。测试可以用 hold、abort 改变某个请求的
 * 返回时机或结果，用来制造“响应很慢”“请求没收到响应”这类情况；权限、数据的变化直接改内存里的数据。
 *
 * 发送消息时，助手的回复按 replies 里排好的脚本生成。保存规则和真实后端一致：用户消息先保存；
 * 确认卡片保存后才推送；回复只有发出 message.done 才保存。脚本没写 done 也没写 error，表示连接中途断开。
 *
 * 知识文档上传后是“待处理”。后台建索引的进度由测试直接修改 knowledge 里的数据来模拟。
 */

import type { Page, Request, Route } from "@playwright/test";

import type { ActionView } from "@/lib/actions";
import type { KnowledgeDocument, StaffReturn, StaffTicket } from "@/lib/admin/api";
import type { ChatMessage, User } from "@/lib/api";
import type { Citation } from "@/lib/citations";
import type { Conversation } from "@/lib/customer/api";

/** 前端的后端地址在测试里指向本站的这个路径（见 playwright.config.ts）。 */
export const FAKE_API_PATH = "/fake-api";

export const CAROL: User = { id: 2, username: "carol", role: "customer" };
export const ADMIN: User = { id: 1, username: "admin", role: "admin" };

const NOW = "2026-10-26T02:00:00Z";

export function ticket(changes: Partial<StaffTicket> & { id: number }): StaffTicket {
  return {
    order_id: null,
    summary: `工单 ${changes.id} 的问题摘要`,
    status: "open",
    resolution: null,
    created_at: NOW,
    resolved_at: null,
    customer: "carol",
    conversation_id: null,
    resolved_by: null,
    ...changes,
  };
}

export function returnRequest(changes: Partial<StaffReturn> & { id: number }): StaffReturn {
  return {
    order_id: 6,
    order_item_id: 9,
    product_name: "SoundPods 降噪耳机",
    quantity: 1,
    refund_cents: 59900,
    reason: "不想要了",
    status: "pending",
    resolution_note: null,
    created_at: NOW,
    resolved_at: null,
    customer: "carol",
    delivered_at: "2026-10-24T02:00:00Z",
    resolved_by: null,
    ...changes,
  };
}

/** 一篇知识文档，默认已就绪、已启用。 */
export function knowledgeDocument(
  changes: Partial<KnowledgeDocument> & { id: number; name: string; title: string },
): KnowledgeDocument {
  return {
    version: 1,
    status: "ready",
    error: null,
    enabled: true,
    passages: 2,
    indexed_at: NOW,
    updated_at: NOW,
    ...changes,
  };
}

/** 一张待确认的退货申请卡片。 */
export function returnAction(changes: Partial<ActionView> & { id: number }): ActionView {
  return {
    kind: "return_request",
    status: "pending",
    error: null,
    expires_at: "2099-01-01T00:00:00Z",
    preview: {
      order_id: 6,
      order_item_id: 9,
      product_name: "SoundPods 降噪耳机",
      quantity: 1,
      refund_cents: 59900,
      reason: "不想要了",
    },
    result: null,
    ...changes,
  } as ActionView;
}

/** 一张待确认的下单卡片：两种商品。 */
export function orderAction(changes: Partial<ActionView> & { id: number }): ActionView {
  return {
    kind: "order",
    status: "pending",
    error: null,
    expires_at: "2099-01-01T00:00:00Z",
    preview: {
      lines: [
        { product_id: 1, product_name: "SoundPods 降噪耳机", unit_price_cents: 59900, quantity: 1 },
        { product_id: 2, product_name: "USB-C 快充线 1 米", unit_price_cents: 3900, quantity: 2 },
      ],
      total_cents: 67700,
    },
    result: null,
    ...changes,
  } as ActionView;
}

/** 助手回复脚本的一步。card 表示助手提议了一项待确认操作；done 可以带上回复引用的知识库原文。 */
export type ReplyStep =
  | { type: "delta"; text: string }
  | { type: "card"; action: ActionView }
  | { type: "done"; citations?: Citation[] }
  | { type: "error"; detail: string };

type Reply = { status: number; body?: unknown; sse?: string };
type Handler = (route: Route, request: Request) => Promise<void>;
type Override = { method: string; path: RegExp; handle: Handler };

/** 被拦住的请求：arrived 在请求到达时完成；调用 release() 后才发出回应。 */
export type HeldRequest = { arrived: Promise<void>; release: () => void };

export class FakeBackend {
  users: Record<string, User> = { carol: CAROL, admin: ADMIN };
  currentUser: User | null = null;
  tickets: StaffTicket[] = [];
  transcripts = new Map<number, ChatMessage[]>();
  returns: StaffReturn[] = [];
  knowledge: KnowledgeDocument[] = [];
  conversations: Conversation[] = [];
  /** 每个会话的消息。确认卡片消息里的 action 和 actions 里的是同一个对象，确认后两边一起变。 */
  messages = new Map<number, ChatMessage[]>();
  actions = new Map<number, ActionView>();
  /** 接下来几次发送消息时，助手依次按这些脚本回复。 */
  replies: ReplyStep[][] = [];
  private nextMessageId = 1000;
  /** 收到的请求，比如 "GET /api/admin/tickets?status=open" */
  readonly requests: string[] = [];
  private overrides: Override[] = [];

  async install(page: Page): Promise<void> {
    await page.route(`**${FAKE_API_PATH}/**`, (route) => this.dispatch(route));
  }

  /** 下一个匹配的请求改用自定义处理（只生效一次，后加的优先）。path 匹配的是不带查询参数的接口路径。 */
  on(method: string, path: RegExp, handle: Handler): void {
    this.overrides.unshift({ method, path, handle });
  }

  /**
   * 拦住下一个匹配的请求：到达时就按当时的数据算好回应（写请求也在这时生效），release() 之后才发出。
   * 模拟“服务器早就处理完了，只是响应很慢”。
   */
  hold(method: string, path: RegExp): HeldRequest {
    let arrive = () => {};
    let release = () => {};
    const arrived = new Promise<void>((resolve) => (arrive = resolve));
    const released = new Promise<void>((resolve) => (release = resolve));
    this.on(method, path, async (route, request) => {
      const reply = this.answer(request);
      arrive();
      await released;
      // 页面可能已经取消了这个请求，这时回应会失败，忽略即可
      await fulfill(route, reply).catch(() => {});
    });
    return { arrived, release };
  }

  /** 下一个匹配的请求断开、收不到响应。processed 为 true 时先照常处理，模拟“服务器处理了，但响应丢了”。 */
  abort(method: string, path: RegExp, { processed = false } = {}): void {
    this.on(method, path, async (route, request) => {
      if (processed) this.answer(request);
      await route.abort("failed");
    });
  }

  private async dispatch(route: Route): Promise<void> {
    const request = route.request();
    const { search } = new URL(request.url());
    const path = apiPath(request);
    this.requests.push(`${request.method()} ${path}${search}`);

    const override = this.overrides.find((item) => item.method === request.method() && item.path.test(path));
    if (override) {
      this.overrides = this.overrides.filter((item) => item !== override);
      await override.handle(route, request);
      return;
    }
    await fulfill(route, this.answer(request));
  }

  /** 按真实接口的约定算出回应。写请求会修改内存里的数据。回应内容是当时数据的副本，之后的修改不影响它。 */
  answer(request: Request): Reply {
    const reply = this.compute(request);
    return reply.body === undefined ? reply : { status: reply.status, body: structuredClone(reply.body) };
  }

  /** 准备一个已有的会话和它的消息。卡片消息的 action 会同时登记到 actions 里。 */
  addConversation(conversation: Conversation, messages: ChatMessage[] = []): void {
    this.conversations.push(conversation);
    this.messages.set(conversation.id, messages);
    for (const message of messages) {
      if (message.action) this.actions.set(message.action.id, message.action);
    }
  }

  /** 收到的某种请求有几次，比如 count("POST", "/api/actions/7/confirm")。 */
  count(method: string, path: string): number {
    return this.requests.filter((item) => item === `${method} ${path}`).length;
  }

  private compute(request: Request): Reply {
    const method = request.method();
    const path = apiPath(request);
    const query = new URL(request.url()).searchParams;
    const body = readJsonBody(request);

    if (method === "POST" && path === "/api/auth/login") {
      const user = this.users[String(body.username)];
      if (!user) return { status: 401, body: { detail: "用户名或密码错误" } };
      this.currentUser = user;
      return { status: 200, body: user };
    }
    if (method === "POST" && path === "/api/auth/logout") {
      this.currentUser = null;
      return { status: 204 };
    }
    if (!this.currentUser) return { status: 401, body: { detail: "未登录或登录已过期" } };
    if (method === "GET" && path === "/api/auth/me") return { status: 200, body: this.currentUser };

    if (path.startsWith("/api/conversations") || path.startsWith("/api/actions/")) {
      return this.answerCustomer(method, path, body);
    }

    if (path.startsWith("/api/admin/")) {
      if (this.currentUser.role !== "admin") return { status: 403, body: { detail: "需要管理员权限" } };
      if (path.startsWith("/api/admin/knowledge/")) return this.answerKnowledge(method, path, request);
      return this.answerAdmin(method, path, query, body);
    }
    return { status: 404, body: { detail: `模拟后端没有这个接口：${method} ${path}` } };
  }

  private answerCustomer(method: string, path: string, body: Record<string, unknown>): Reply {
    if (method === "GET" && path === "/api/conversations") return { status: 200, body: newestFirst(this.conversations) };
    if (method === "POST" && path === "/api/conversations") {
      const conversation = { id: this.conversations.length + 1, title: "新会话", created_at: NOW };
      this.addConversation(conversation);
      return { status: 201, body: conversation };
    }

    const messagesMatch = path.match(/^\/api\/conversations\/(\d+)\/messages$/);
    if (messagesMatch) {
      const messages = this.messages.get(Number(messagesMatch[1]));
      if (!messages) return { status: 404, body: { detail: "会话不存在" } };
      if (method === "GET") return { status: 200, body: messages };
      return { status: 200, sse: this.reply(Number(messagesMatch[1]), messages, String(body.content)) };
    }

    const actionMatch = path.match(/^\/api\/actions\/(\d+)(\/confirm|\/cancel)?$/);
    if (actionMatch) {
      const action = this.actions.get(Number(actionMatch[1]));
      if (!action) return { status: 404, body: { detail: "操作不存在" } };
      if (method === "GET" && !actionMatch[2]) return { status: 200, body: action };
      if (action.status !== "pending") return { status: 409, body: { detail: "这项操作已经处理过了", action } };
      if (actionMatch[2] === "/cancel") {
        action.status = "cancelled";
      } else {
        Object.assign(action, { status: "executed", result: { order_id: 21, return_id: 12, ticket_id: 3 } });
      }
      return { status: 200, body: action };
    }
    return { status: 404, body: { detail: `模拟后端没有这个接口：${method} ${path}` } };
  }

  /** 按下一个脚本生成回复，边“推送”边保存，返回整段 SSE 文本。 */
  private reply(conversationId: number, messages: ChatMessage[], content: string): string {
    const conversation = this.conversations.find((item) => item.id === conversationId);
    if (conversation && messages.length === 0) conversation.title = content.slice(0, 20);
    const save = (message: Omit<ChatMessage, "id" | "created_at">) => {
      const saved = { ...message, id: this.nextMessageId++, created_at: NOW };
      messages.push(saved);
      return saved;
    };

    const user = save({ role: "user", content, action: null });
    let sse = event("message.accepted", { message_id: user.id });
    let text = "";
    for (const step of this.replies.shift() ?? [{ type: "done" }]) {
      if (step.type === "delta") {
        text += step.text;
        sse += event("message.delta", { text: step.text });
      } else if (step.type === "card") {
        this.actions.set(step.action.id, step.action);
        const card = save({ role: "assistant", content: "", action: step.action });
        sse += event("action.proposed", { message_id: card.id, action: step.action });
      } else if (step.type === "done") {
        const citations = step.citations ?? [];
        const reply = save({ role: "assistant", content: text, action: null, citations });
        sse += event("message.done", { message_id: reply.id, citations });
      } else {
        sse += event("error", { detail: step.detail });
      }
    }
    return sse;
  }

  private answerAdmin(method: string, path: string, query: URLSearchParams, body: Record<string, unknown>): Reply {
    const admin = this.currentUser?.username ?? null;

    if (method === "GET" && path === "/api/admin/tickets") {
      const status = query.get("status") ?? "open";
      return { status: 200, body: newestFirst(this.tickets.filter((item) => item.status === status)) };
    }
    const ticketMatch = path.match(/^\/api\/admin\/tickets\/(\d+)(\/close)?$/);
    if (ticketMatch) {
      const id = Number(ticketMatch[1]);
      const found = this.tickets.find((item) => item.id === id);
      if (!found) return { status: 404, body: { detail: `工单 ${id} 不存在` } };
      if (method === "GET" && !ticketMatch[2]) {
        return { status: 200, body: { ticket: found, transcript: this.transcripts.get(id) ?? [] } };
      }
      if (method === "POST" && ticketMatch[2]) {
        if (found.status === "closed") {
          return { status: 409, body: { detail: "工单已经关闭，不能再处理", ticket: found } };
        }
        const resolution = String(body.resolution ?? "").trim();
        if (!resolution) return { status: 422, body: { detail: "请填写处理结果（不超过 2000 个字）" } };
        Object.assign(found, { status: "closed", resolution, resolved_by: admin, resolved_at: NOW });
        return { status: 200, body: found };
      }
    }

    if (method === "GET" && path === "/api/admin/returns") {
      const status = query.get("status") ?? "pending";
      return { status: 200, body: newestFirst(this.returns.filter((item) => item.status === status)) };
    }
    const returnMatch = path.match(/^\/api\/admin\/returns\/(\d+)(\/complete|\/reject)?$/);
    if (returnMatch) {
      const id = Number(returnMatch[1]);
      const found = this.returns.find((item) => item.id === id);
      if (!found) return { status: 404, body: { detail: `退货申请 ${id} 不存在` } };
      if (method === "GET" && !returnMatch[2]) return { status: 200, body: found };
      if (method === "POST" && returnMatch[2]) {
        if (found.status !== "pending") {
          return { status: 409, body: { detail: `退货申请${FINISHED_TEXT[found.status]}，不能再处理`, return: found } };
        }
        const note = String(body.note ?? body.reason ?? "").trim() || null;
        if (returnMatch[2] === "/reject" && !note) {
          return { status: 422, body: { detail: "请填写拒绝原因（不超过 500 个字）" } };
        }
        const status = returnMatch[2] === "/complete" ? "completed" : "rejected";
        Object.assign(found, { status, resolution_note: note, resolved_by: admin, resolved_at: NOW });
        return { status: 200, body: found };
      }
    }
    return { status: 404, body: { detail: `模拟后端没有这个接口：${method} ${path}` } };
  }

  /** 知识文档：列表按名称排序；上传和重新索引后是“待处理”，之后的进度由测试修改 knowledge 来模拟。 */
  private answerKnowledge(method: string, path: string, request: Request): Reply {
    if (path === "/api/admin/knowledge/documents") {
      if (method === "GET") {
        return { status: 200, body: [...this.knowledge].sort((a, b) => (a.name < b.name ? -1 : 1)) };
      }
      if (method === "POST") return this.upload(request);
    }

    const match = path.match(/^\/api\/admin\/knowledge\/documents\/(\d+)\/(reindex|enable|disable)$/);
    if (method === "POST" && match) {
      const id = Number(match[1]);
      const found = this.knowledge.find((item) => item.id === id);
      if (!found) return { status: 404, body: { detail: `知识文档 ${id} 不存在` } };
      if (match[2] === "reindex") {
        Object.assign(found, { status: "pending", error: null, passages: null });
        return { status: 202, body: found };
      }
      found.enabled = match[2] === "enable";
      return { status: 200, body: found };
    }
    return { status: 404, body: { detail: `模拟后端没有这个接口：${method} ${path}` } };
  }

  /** 和真实接口一样：只收 .md；第一行不是“# 标题”返回 422；同名就是更新，版本加 1。 */
  private upload(request: Request): Reply {
    const file = readUploadedFile(request);
    if (!file || !file.filename.toLowerCase().endsWith(".md")) {
      return { status: 422, body: { detail: "只支持 .md 文件" } };
    }
    const name = file.filename.slice(0, -3);
    const title = file.text.trimStart().match(/^# (.+)/)?.[1]?.trim();
    if (!title) return { status: 422, body: { detail: `${name}：第一行必须是“# 文档标题”` } };

    const changes = { title, status: "pending", error: null, passages: null, updated_at: NOW } as const;
    const existing = this.knowledge.find((item) => item.name === name);
    if (existing) {
      Object.assign(existing, { ...changes, version: existing.version + 1 });
      return { status: 202, body: existing };
    }
    const created = knowledgeDocument({ id: this.knowledge.length + 1, name, ...changes, indexed_at: null });
    this.knowledge.push(created);
    return { status: 202, body: created };
  }
}

const FINISHED_TEXT: Record<StaffReturn["status"], string> = {
  pending: "还在处理中",
  completed: "已经完成",
  rejected: "已经被拒绝",
  cancelled: "已经撤回",
};

/** 和后端一样：最新的在前，最多 50 条。 */
function newestFirst<T extends { id: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.id - a.id).slice(0, 50);
}

/** 接口路径：去掉 /fake-api 前缀和查询参数，比如 "/api/admin/tickets"。 */
function apiPath(request: Request): string {
  const { pathname } = new URL(request.url());
  return pathname.slice(pathname.indexOf(FAKE_API_PATH) + FAKE_API_PATH.length);
}

/** JSON 请求体；没有请求体，或者不是 JSON（比如上传文件的 multipart）时当作空的。 */
function readJsonBody(request: Request): Record<string, unknown> {
  try {
    return (request.postDataJSON() ?? {}) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** 从 multipart 请求体里取出上传的文件名和文本内容。 */
function readUploadedFile(request: Request): { filename: string; text: string } | null {
  const match = (request.postData() ?? "").match(/filename="([^"]*)"[\s\S]*?\r\n\r\n([\s\S]*?)\r\n--/);
  return match ? { filename: match[1], text: match[2] } : null;
}

function event(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

function fulfill(route: Route, reply: Reply): Promise<void> {
  if (reply.sse !== undefined) {
    return route.fulfill({ status: reply.status, contentType: "text/event-stream", body: reply.sse });
  }
  if (reply.body === undefined) return route.fulfill({ status: reply.status });
  return route.fulfill({ status: reply.status, contentType: "application/json", body: JSON.stringify(reply.body) });
}
