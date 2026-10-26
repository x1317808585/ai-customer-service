import { expect, test } from "@playwright/test";

import { ADMIN, CAROL, FakeBackend } from "./fakeBackend";

async function logIn(page: import("@playwright/test").Page, username: string) {
  await page.goto("/login");
  await page.getByPlaceholder("用户名").fill(username);
  await page.getByPlaceholder("密码").fill("password");
  await page.getByRole("button", { name: "登录" }).click();
}

test("顾客登录后进入聊天页，看不到管理工作台入口", async ({ page }) => {
  const backend = new FakeBackend();
  await backend.install(page);

  await logIn(page, CAROL.username);

  await expect(page).toHaveURL("/");
  await expect(page.getByText("有什么可以帮你？")).toBeVisible();
  await expect(page.getByRole("link", { name: "管理工作台" })).toHaveCount(0);
});

test("管理员登录后进入管理工作台", async ({ page }) => {
  const backend = new FakeBackend();
  await backend.install(page);

  await logIn(page, ADMIN.username);

  await expect(page).toHaveURL(/\/admin/);
});

test("管理员打开聊天页时，侧边栏有管理工作台入口", async ({ page }) => {
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  await backend.install(page);

  await page.goto("/");

  await expect(page.getByRole("link", { name: "管理工作台" })).toHaveAttribute("href", "/admin");
});
