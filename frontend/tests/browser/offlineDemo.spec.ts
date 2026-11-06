import { expect, test, type Page } from "@playwright/test";

import { CAROL, FakeBackend } from "./fakeBackend";

const ASSISTANT = /^\/api\/assistant$/;

async function openChat(page: Page, offline: boolean): Promise<FakeBackend> {
  const backend = new FakeBackend();
  backend.currentUser = CAROL;
  backend.on("GET", ASSISTANT, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ offline }) }),
  );
  await backend.install(page);
  await page.goto("/");
  return backend;
}

test("离线模型：显示离线演示说明；点带编号的说法只填前半句，编号由用户补上", async ({ page }) => {
  await openChat(page, true);

  await page.getByText("离线演示模式").click();
  const examples = page.getByRole("list", { name: "支持的说法" });
  await expect(examples).toContainText("下单 商品 N");
  await examples.getByRole("button", { name: /^下单/ }).click();

  await expect(page.getByLabel("输入消息")).toHaveValue("下单 商品 ");
  await expect(page.getByLabel("输入消息")).toBeFocused();
});

test("真实模型：不显示离线演示说明", async ({ page }) => {
  const backend = await openChat(page, false);

  await expect.poll(() => backend.count("GET", "/api/assistant")).toBe(1);
  await expect(page.getByLabel("输入消息")).toBeVisible();
  await expect(page.getByText("离线演示模式")).toHaveCount(0);
});
