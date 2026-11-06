import { expect, test, type Page } from "@playwright/test";

import { CAROL, FakeBackend } from "./fakeBackend";

test.use({ viewport: { width: 1280, height: 800 } });

const CONVERSATION = { id: 1, title: "查询", created_at: "2026-10-26T02:00:00Z" };
const MESSAGES = /^\/api\/conversations\/1\/messages$/;

const messageList = (page: Page) => page.getByRole("list", { name: "消息记录" });
const input = (page: Page) => page.getByLabel("输入消息");

async function send(page: Page, text: string) {
  await input(page).fill(text);
  await page.getByRole("button", { name: "发送" }).click();
}

function sse(type: string, data: unknown): string {
  return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

test("流里夹着心跳：回复照常完成，心跳不显示", async ({ page }) => {
  const backend = new FakeBackend();
  backend.currentUser = CAROL;
  backend.addConversation(CONVERSATION);
  backend.on("POST", MESSAGES, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: [
        sse("message.accepted", { message_id: 100 }),
        sse("message.delta", { text: "好的，" }),
        sse("heartbeat", {}),
        sse("message.delta", { text: "马上为你查询" }),
        sse("message.done", { message_id: 101, citations: [] }),
      ].join(""),
    }),
  );
  await backend.install(page);
  await page.goto("/");

  await send(page, "帮我查一下");

  const items = messageList(page).getByRole("listitem");
  await expect(items).toHaveCount(2);
  await expect(items.nth(1)).toHaveText("好的，马上为你查询");
  await expect(page.locator("body")).not.toContainText("heartbeat");
});

test("45 秒没收到任何事件：中止请求，重新读取会话记录核对，不重发消息", async ({ page }) => {
  await page.clock.install();
  const backend = new FakeBackend();
  backend.currentUser = CAROL;
  backend.addConversation(CONVERSATION);
  backend.replies.push([{ type: "delta", text: "已经帮你查到了" }, { type: "done" }]);
  // 服务器到达时就处理完了（回复已经保存），但响应一直没有发回来
  const stalled = backend.hold("POST", MESSAGES);
  await backend.install(page);
  await page.goto("/");

  await send(page, "帮我查一下");
  await stalled.arrived;
  await page.clock.fastForward(45_000);

  await expect(page.getByRole("status").filter({ hasText: "消息可能已发送，请先核对会话记录" })).toBeVisible();
  await expect(messageList(page)).toContainText("已经帮你查到了");
  await expect(input(page)).toHaveValue("帮我查一下");
  expect(backend.count("POST", "/api/conversations/1/messages")).toBe(1);
  stalled.release();
});
