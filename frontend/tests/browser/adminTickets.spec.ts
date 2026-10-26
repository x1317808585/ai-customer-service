import { expect, test, type Page } from "@playwright/test";

import { ADMIN, FakeBackend, ticket } from "./fakeBackend";

const DETAIL = /^\/api\/admin\/tickets\/1$/;
const CLOSE = /^\/api\/admin\/tickets\/1\/close$/;

/** 以管理员身份打开工单页。setup 里准备模拟后端的数据和特殊行为。 */
async function openDesk(page: Page, url: string, setup: (backend: FakeBackend) => void): Promise<FakeBackend> {
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  setup(backend);
  await backend.install(page);
  await page.goto(url);
  return backend;
}

const detail = (page: Page) => page.getByRole("region", { name: "详情" });
const resolutionInput = (page: Page) => page.getByLabel("处理结果（顾客能看到）");
const closeButton = (page: Page) => page.getByRole("button", { name: "关闭工单" });
/** 页面顶部的操作提示（成功、需要注意、出错） */
const notice = (page: Page) => page.getByLabel("操作提示");
const accessDenied = (page: Page) => page.getByRole("alert").filter({ hasText: "没有权限访问管理工作台" });

async function close(page: Page, resolution: string) {
  await resolutionInput(page).fill(resolution);
  await closeButton(page).click();
}

/** 给晚到的响应留出时间，再检查页面没有被它改掉。 */
const letLateResponsesArrive = (page: Page) => page.waitForTimeout(300);

test("查看工单详情和来源会话的聊天记录，聊天记录里的确认卡片没有按钮", async ({ page }) => {
  await openDesk(page, "/admin/tickets", (backend) => {
    backend.tickets = [ticket({ id: 1, order_id: 6, conversation_id: 5, summary: "顾客反馈包裹一直没有送到" })];
    backend.transcripts.set(1, [
      { id: 1, role: "user", content: "我的包裹还没到", created_at: "2026-10-26T01:00:00Z", action: null },
      {
        id: 2,
        role: "assistant",
        content: "",
        created_at: "2026-10-26T01:01:00Z",
        action: {
          id: 7,
          kind: "ticket",
          status: "pending",
          error: null,
          expires_at: "2099-01-01T00:00:00Z",
          preview: { order_id: 6, summary: "顾客反馈包裹一直没有送到" },
          result: null,
        },
      },
    ]);
  });

  await page.getByRole("link", { name: /#1/ }).click();

  await expect(page).toHaveURL("/admin/tickets?status=open&id=1");
  await expect(detail(page).getByRole("heading", { name: "工单 #1" })).toBeVisible();
  const transcript = page.getByRole("list", { name: "来源会话的聊天记录" });
  await expect(transcript).toContainText("我的包裹还没到");
  await expect(transcript).toContainText("转人工");
  await expect(transcript.getByRole("button")).toHaveCount(0);
});

test("关闭工单：处理结果必填；关闭后从待处理列表消失，详情保留最终状态", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });

  await expect(closeButton(page)).toBeDisabled();
  await close(page, "已联系物流补发");

  await expect(notice(page).filter({ hasText: "工单 #1 已关闭" })).toBeVisible();
  await expect(detail(page)).toContainText("状态：已关闭");
  await expect(detail(page)).toContainText("已联系物流补发");
  await expect(detail(page)).toContainText("admin");
  await expect(page.getByText("暂无待处理的工单")).toBeVisible();
  expect(backend.requests.filter((request) => request.startsWith("POST"))).toEqual(["POST /api/admin/tickets/1/close"]);
});

test("快速切换：先点的工单响应慢，最后显示的是后点的那张", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets", (backend) => {
    backend.tickets = [ticket({ id: 1 }), ticket({ id: 2 })];
  });
  const slow = backend.hold("GET", DETAIL);

  await page.getByRole("link", { name: /#1/ }).click();
  await slow.arrived;
  await page.getByRole("link", { name: /#2/ }).click();
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
  slow.release();
  await letLateResponsesArrive(page);

  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
});

test("同一张工单的旧读取晚到，不会把已关闭改回待处理", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });
  await expect(resolutionInput(page)).toBeVisible();
  const staleRead = backend.hold("GET", DETAIL);

  await page.getByRole("button", { name: "刷新" }).click();
  await staleRead.arrived;
  await close(page, "已处理");
  await expect(detail(page)).toContainText("状态：已关闭");
  staleRead.release();
  await letLateResponsesArrive(page);

  await expect(detail(page)).toContainText("状态：已关闭");
});

test("提交时切到另一张工单：结果不会显示到那张工单上，提示里带的是原来的编号", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 }), ticket({ id: 2 })];
  });
  const slowClose = backend.hold("POST", CLOSE);

  await close(page, "已处理");
  await slowClose.arrived;
  await page.getByRole("link", { name: /#2/ }).click();
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
  slowClose.release();

  await expect(notice(page).filter({ hasText: "工单 #1 已关闭" })).toBeVisible();
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
  await expect(detail(page)).toContainText("状态：待处理");
  await expect(resolutionInput(page)).toHaveValue("");
});

test("409：另一位管理员已经关闭，页面显示最新状态，不能再提交", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });
  await expect(resolutionInput(page)).toBeVisible();
  Object.assign(backend.tickets[0], { status: "closed", resolution: "已退款", resolved_by: "admin2" });

  await close(page, "已处理");

  await expect(notice(page)).toContainText("工单已经关闭，不能再处理");
  await expect(detail(page)).toContainText("状态：已关闭");
  await expect(detail(page)).toContainText("admin2");
  await expect(resolutionInput(page)).toHaveCount(0);
});

test("403：管理员身份被撤销后清空页面，晚到的响应也不会让数据重新出现", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 }), ticket({ id: 2 })];
  });
  await expect(resolutionInput(page)).toBeVisible();
  const slowList = backend.hold("GET", /^\/api\/admin\/tickets$/);
  await page.getByRole("button", { name: "刷新" }).click();
  await slowList.arrived;
  backend.currentUser = { ...ADMIN, role: "customer" };

  await close(page, "已处理");
  await expect(accessDenied(page)).toBeVisible();
  slowList.release();
  await letLateResponsesArrive(page);

  await expect(accessDenied(page)).toBeVisible();
  await expect(page.getByRole("link", { name: /#2/ })).toHaveCount(0);
});

test("提交没收到响应，重新查询到已关闭：说明不能确定是不是这次完成的", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });
  backend.abort("POST", CLOSE, { processed: true });

  await close(page, "已处理");

  await expect(notice(page)).toContainText("当前状态是“已关闭”，但不能确定是不是这次提交完成的");
  await expect(detail(page)).toContainText("状态：已关闭");
});

test("提交没收到响应，重新查询到仍待处理：不说失败，可以重新提交", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });
  backend.abort("POST", CLOSE);

  await close(page, "已处理");

  await expect(notice(page)).toContainText("当前仍显示“待处理”");
  await expect(notice(page)).not.toContainText("失败");
  await expect(notice(page).getByRole("button", { name: "重新查询" })).toBeVisible();
  await expect(resolutionInput(page)).toHaveValue("已处理");
  await closeButton(page).click();
  await expect(notice(page).filter({ hasText: "工单 #1 已关闭" })).toBeVisible();
});

test("提交没收到响应，重新查询也失败：确认之前不能再提交", async ({ page }) => {
  const backend = await openDesk(page, "/admin/tickets?status=open&id=1", (backend) => {
    backend.tickets = [ticket({ id: 1 })];
  });
  await expect(resolutionInput(page)).toBeVisible();
  backend.abort("POST", CLOSE);
  backend.abort("GET", DETAIL);

  await close(page, "已处理");

  await expect(notice(page)).toContainText("结果暂时无法确认");
  await expect(closeButton(page)).toBeDisabled();
  await notice(page).getByRole("button", { name: "重新查询" }).click();
  await expect(notice(page)).toContainText("当前仍显示“待处理”");
  await expect(closeButton(page)).toBeEnabled();
});

test("地址栏：不合法的状态换成默认值；无效编号、不存在的编号有提示；不在列表里的工单也能打开", async ({ page }) => {
  await openDesk(page, "/admin/tickets?status=bogus", (backend) => {
    backend.tickets = [ticket({ id: 1 }), ticket({ id: 7, status: "closed", resolution: "已处理" })];
  });
  await expect(page).toHaveURL("/admin/tickets?status=open");

  await page.goto("/admin/tickets?status=open&id=abc");
  await expect(detail(page)).toContainText("编号无效");

  await page.goto("/admin/tickets?status=open&id=99");
  await expect(detail(page)).toContainText("记录不存在");

  await page.goto("/admin/tickets?status=open&id=7");
  await expect(detail(page).getByRole("heading", { name: "工单 #7" })).toBeVisible();
  await expect(detail(page)).toContainText("状态：已关闭");
});

test("浏览器前进、后退时，页面和地址栏保持一致", async ({ page }) => {
  await openDesk(page, "/admin/tickets", (backend) => {
    backend.tickets = [ticket({ id: 1 }), ticket({ id: 2 })];
  });

  await page.getByRole("link", { name: /#1/ }).click();
  await expect(detail(page).getByRole("heading", { name: "工单 #1" })).toBeVisible();
  await page.getByRole("link", { name: /#2/ }).click();
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
  await page.getByRole("link", { name: "已关闭" }).click();
  await expect(page).toHaveURL("/admin/tickets?status=closed");
  await expect(detail(page)).toContainText("从左侧选择一张工单");

  await page.goBack();
  await expect(page).toHaveURL("/admin/tickets?status=open&id=2");
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
  await page.goBack();
  await expect(detail(page).getByRole("heading", { name: "工单 #1" })).toBeVisible();
  await page.goForward();
  await expect(detail(page).getByRole("heading", { name: "工单 #2" })).toBeVisible();
});

test("列表正好返回 50 条时，提示可能还有更早的记录", async ({ page }) => {
  await openDesk(page, "/admin/tickets", (backend) => {
    backend.tickets = Array.from({ length: 55 }, (_, index) => ticket({ id: index + 1 }));
  });

  await expect(page.getByRole("list", { name: "待处理的工单" }).getByRole("listitem")).toHaveCount(50);
  await expect(page.getByText("可能还有更早的记录")).toBeVisible();
});
