import { expect, test, type Page } from "@playwright/test";

import { CAROL, FakeBackend, returnAction } from "./fakeBackend";

test.use({ viewport: { width: 1280, height: 800 } });

const CONVERSATION = { id: 1, title: "退货", created_at: "2026-10-26T02:00:00Z" };
const CONFIRM = /^\/api\/actions\/7\/confirm$/;

/** 以顾客身份打开聊天页。setup 里准备会话、消息和回复脚本。 */
async function openChat(page: Page, setup: (backend: FakeBackend) => void = () => {}): Promise<FakeBackend> {
  const backend = new FakeBackend();
  backend.currentUser = CAROL;
  setup(backend);
  await backend.install(page);
  await page.goto("/");
  return backend;
}

const messageList = (page: Page) => page.getByRole("list", { name: "消息记录" });
const card = (page: Page) => page.getByRole("article", { name: "退货申请" });
const confirmButton = (page: Page) => card(page).getByRole("button", { name: "确认提交" });

async function send(page: Page, text: string) {
  await page.getByLabel("输入消息").fill(text);
  await page.getByRole("button", { name: "发送" }).click();
}

test("顺序是用户消息、确认卡片、助手说明，刷新后不变", async ({ page }) => {
  await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION);
    backend.replies.push([
      { type: "card", action: returnAction({ id: 7 }) },
      { type: "delta", text: "请核对上方卡片" },
      { type: "done" },
    ]);
  });

  await send(page, "我想退货，订单项 9");
  await expectOrder(page);

  await page.reload();
  await expectOrder(page);
});

async function expectOrder(page: Page) {
  const items = messageList(page).getByRole("listitem");
  await expect(items).toHaveCount(3);
  await expect(items.nth(0)).toHaveText("我想退货，订单项 9");
  await expect(items.nth(1).getByRole("article", { name: "退货申请" })).toBeVisible();
  await expect(items.nth(2)).toHaveText("请核对上方卡片");
}

test("卡片保存后连接断开：卡片和用户消息都只出现一次，不残留半截回复", async ({ page }) => {
  await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION);
    backend.replies.push([
      { type: "card", action: returnAction({ id: 7 }) },
      { type: "delta", text: "请核对" },
    ]);
  });

  await send(page, "我想退货，订单项 9");

  await expect(page.getByRole("status")).toContainText("连接中断");
  await expect(messageList(page).getByRole("listitem")).toHaveCount(2);
  await expect(card(page)).toHaveCount(1);
  await expect(messageList(page).getByText("我想退货，订单项 9", { exact: true })).toHaveCount(1);
  await expect(messageList(page).getByText("请核对", { exact: true })).toHaveCount(0);
});

test("确认已经成功但响应丢了：重新查询后显示已提交，只确认了一次", async ({ page }) => {
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      { id: 1, role: "assistant", content: "", created_at: "2026-10-26T02:00:00Z", action: returnAction({ id: 7 }) },
    ]);
    backend.abort("POST", CONFIRM, { processed: true });
  });

  await confirmButton(page).click();

  await expect(card(page)).toContainText("已提交，退货单号 12");
  await expect(confirmButton(page)).toHaveCount(0);
  expect(backend.count("POST", "/api/actions/7/confirm")).toBe(1);
});

test("提交中不能重复提交", async ({ page }) => {
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      { id: 1, role: "assistant", content: "", created_at: "2026-10-26T02:00:00Z", action: returnAction({ id: 7 }) },
    ]);
  });
  const held = backend.hold("POST", CONFIRM);

  await confirmButton(page).dblclick();
  await held.arrived;

  await expect(card(page)).toContainText("提交中");
  await expect(confirmButton(page)).toBeDisabled();
  held.release();
  await expect(card(page)).toContainText("已提交");
  expect(backend.count("POST", "/api/actions/7/confirm")).toBe(1);
});

test("到了有效期，卡片切换成已过期，不再显示按钮", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-10-26T02:00:00Z") });
  await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      {
        id: 1,
        role: "assistant",
        content: "",
        created_at: "2026-10-26T02:00:00Z",
        action: returnAction({ id: 7, expires_at: "2026-10-26T02:10:00Z" }),
      },
    ]);
  });
  await expect(confirmButton(page)).toBeVisible();

  await page.clock.fastForward("10:01");

  await expect(card(page)).toContainText("已过期");
  await expect(card(page).getByRole("button")).toHaveCount(0);
});

test("没有空格的长消息自动换行，页面没有横向滚动", async ({ page }) => {
  await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      { id: 1, role: "user", content: "A".repeat(400), created_at: "2026-10-26T02:00:00Z", action: null },
    ]);
  });
  await expect(messageList(page)).toBeVisible();

  // 页面本身和消息区（消息列表外面那层滚动容器）都不能横向滚动
  const pageFits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  const listFits = await messageList(page).evaluate((list) => {
    const scroller = list.parentElement!;
    return scroller.scrollWidth <= scroller.clientWidth;
  });
  expect([pageFits, listFits]).toEqual([true, true]);
});
