import { expect, test } from "@playwright/test";

import { ADMIN, CAROL, FakeBackend, ticket } from "./fakeBackend";

test("没登录时打开管理工作台，跳到登录页", async ({ page }) => {
  const backend = new FakeBackend();
  await backend.install(page);

  await page.goto("/admin/tickets");

  await expect(page).toHaveURL("/login");
});

test("顾客打开管理工作台：显示没有权限，并且不请求任何管理接口", async ({ page }) => {
  const backend = new FakeBackend();
  backend.currentUser = CAROL;
  backend.tickets = [ticket({ id: 1 })];
  await backend.install(page);

  await page.goto("/admin/tickets");

  await expect(page.getByRole("alert").filter({ hasText: "没有权限访问管理工作台" })).toBeVisible();
  await expect(page.getByRole("link", { name: "返回聊天页" })).toHaveAttribute("href", "/");
  expect(backend.requests.filter((request) => request.includes("/api/admin/"))).toEqual([]);
});

test("管理员打开 /admin 进入工单页，导航可以切到退货申请", async ({ page }) => {
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  backend.tickets = [ticket({ id: 1 })];
  await backend.install(page);

  await page.goto("/admin");

  await expect(page).toHaveURL("/admin/tickets?status=open");
  const nav = page.getByRole("navigation", { name: "管理工作台" });
  await expect(nav.getByRole("link", { name: "工单" })).toHaveAttribute("aria-current", "page");
  await expect(nav.getByRole("link", { name: "退货申请" })).toHaveAttribute("href", "/admin/returns");
  await expect(page.getByRole("link", { name: "聊天页" })).toHaveAttribute("href", "/");
});
