/**
 * 浏览器测试用的模拟后端：在浏览器里拦截前端发给后端的请求，用内存里的数据回应。
 *
 * 默认行为按真实接口的约定实现（401 / 403 / 404 / 409 / 422）。测试可以用 hold、abort 改变某个请求的
 * 返回时机或结果，用来制造“响应很慢”“请求没收到响应”这类情况；权限、数据的变化直接改内存里的数据。
 */

import type { Page, Request, Route } from "@playwright/test";

import type { StaffReturn, StaffTicket } from "@/lib/admin/api";
import type { ChatMessage, User } from "@/lib/api";

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

type Reply = { status: number; body?: unknown };
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

  private compute(request: Request): Reply {
    const method = request.method();
    const path = apiPath(request);
    const query = new URL(request.url()).searchParams;
    const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;

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
    if (method === "GET" && path === "/api/conversations") return { status: 200, body: [] };

    if (path.startsWith("/api/admin/")) {
      if (this.currentUser.role !== "admin") return { status: 403, body: { detail: "需要管理员权限" } };
      return this.answerAdmin(method, path, query, body);
    }
    return { status: 404, body: { detail: `模拟后端没有这个接口：${method} ${path}` } };
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

function fulfill(route: Route, reply: Reply): Promise<void> {
  if (reply.body === undefined) return route.fulfill({ status: reply.status });
  return route.fulfill({ status: reply.status, contentType: "application/json", body: JSON.stringify(reply.body) });
}
