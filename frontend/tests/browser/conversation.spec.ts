import { expect, test, type Page } from "@playwright/test";

import { CAROL, FakeBackend, orderAction, returnAction } from "./fakeBackend";

test.use({ viewport: { width: 1280, height: 800 } });

const CONVERSATION = { id: 1, title: "退货", created_at: "2026-10-26T02:00:00Z" };
const CONFIRM = /^\/api\/actions\/7\/confirm$/;
const ACTION = /^\/api\/actions\/7$/;
const MESSAGES = /^\/api\/conversations\/1\/messages$/;

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
const input = (page: Page) => page.getByLabel("输入消息");
const notice = (page: Page) => page.getByRole("status");

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
  await expect(items.nth(0)).toContainText("我想退货，订单项 9");
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

test("没收到服务器确认就断网、会话记录也读不到：不清空页面，输入框留着原文，不说消息没发出", async ({ page }) => {
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      { id: 1, role: "user", content: "之前的问题", created_at: "2026-10-26T02:00:00Z", action: null },
      { id: 2, role: "assistant", content: "", created_at: "2026-10-26T02:00:00Z", action: returnAction({ id: 7 }) },
    ]);
  });
  await expect(card(page)).toBeVisible();
  backend.abort("POST", MESSAGES);
  backend.abort("GET", MESSAGES);

  await send(page, "我想退货，订单项 9");

  await expect(notice(page)).toContainText("暂时无法核对会话记录，消息可能已发送");
  await expect(notice(page)).not.toContainText("没有发出");
  await expect(input(page)).toHaveValue("我想退货，订单项 9");
  await expect(messageList(page).getByText("之前的问题")).toBeVisible();
  await expect(card(page)).toBeVisible();

  await notice(page).getByRole("button", { name: "重新加载会话" }).click();
  await expect(notice(page)).toHaveCount(0);
  await expect(messageList(page).getByText("我想退货，订单项 9")).toHaveCount(0);
});

test("确认后连查询也失败：显示结果待核实，只能重新查询，查到后显示已提交", async ({ page }) => {
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, [
      { id: 1, role: "assistant", content: "", created_at: "2026-10-26T02:00:00Z", action: returnAction({ id: 7 }) },
    ]);
    backend.abort("POST", CONFIRM, { processed: true });
    backend.abort("GET", ACTION);
  });

  await confirmButton(page).click();

  await expect(card(page)).toContainText("结果待核实");
  await expect(confirmButton(page)).toHaveCount(0);
  await expect(card(page).getByRole("button", { name: "取消" })).toHaveCount(0);

  await card(page).getByRole("button", { name: "重新查询" }).click();

  await expect(card(page)).toContainText("已提交，退货单号 12");
  await expect(card(page)).not.toContainText("结果待核实");
  expect(backend.count("POST", "/api/actions/7/confirm")).toBe(1);
});

test("输入法选词时按回车不发送", async ({ page }) => {
  const backend = await openChat(page, (backend) => backend.addConversation(CONVERSATION));
  await input(page).fill("我想退货");

  for (const init of [{ isComposing: true }, { keyCode: 229 }]) {
    await input(page).dispatchEvent("keydown", { key: "Enter", bubbles: true, ...init });
  }

  await expect(input(page)).toHaveValue("我想退货");
  expect(backend.count("POST", "/api/conversations/1/messages")).toBe(0);
  await input(page).press("Enter");
  await expect(input(page)).toHaveValue("");
  expect(backend.count("POST", "/api/conversations/1/messages")).toBe(1);
});

test("往上翻看时，新回复不会把页面拉到底部，而是提示有新消息", async ({ page }) => {
  const history = Array.from({ length: 30 }, (_, index) => ({
    id: index + 1,
    role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
    content: `第 ${index + 1} 条消息`,
    created_at: "2026-10-26T02:00:00Z",
    action: null,
  }));
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION, history);
    backend.replies.push([{ type: "delta", text: "这是新的回复" }, { type: "done" }]);
  });
  const scroller = messageList(page).locator("..");
  const held = backend.hold("POST", MESSAGES);

  await send(page, "再问一个问题");
  await held.arrived;
  await scroller.evaluate((element) => element.scrollTo({ top: 0 }));
  await expect(messageList(page).getByText("第 1 条消息", { exact: true })).toBeInViewport();
  held.release();

  await expect(page.getByRole("button", { name: "有新消息" })).toBeVisible();
  await expect(messageList(page).getByText("第 1 条消息", { exact: true })).toBeInViewport();

  await page.getByRole("button", { name: "有新消息" }).click();
  await expect(messageList(page).getByText("这是新的回复")).toBeInViewport();
  await expect(page.getByRole("button", { name: "有新消息" })).toHaveCount(0);
});

test("下单卡片显示每种商品的数量、单价和合计，确认后显示模拟订单号", async ({ page }) => {
  const backend = await openChat(page, (backend) => {
    backend.addConversation(CONVERSATION);
    backend.replies.push([{ type: "card", action: orderAction({ id: 8 }) }, { type: "done" }]);
  });

  await send(page, "下单商品 1");
  const orderCard = page.getByRole("article", { name: "下单" });

  await expect(orderCard).toContainText("SoundPods 降噪耳机 × 1，单价 ¥599.00");
  await expect(orderCard).toContainText("USB-C 快充线 1 米 × 2，单价 ¥39.00");
  await expect(orderCard).toContainText("合计¥677.00");
  await orderCard.getByRole("button", { name: "确认下单" }).click();

  await expect(orderCard).toContainText("模拟订单已创建，订单号 21");
  await expect(orderCard.getByRole("button")).toHaveCount(0);
  expect(backend.count("POST", "/api/actions/8/confirm")).toBe(1);
});
