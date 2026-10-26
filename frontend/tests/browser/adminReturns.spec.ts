import { expect, test, type Page } from "@playwright/test";

import { ADMIN, FakeBackend, returnRequest } from "./fakeBackend";

/** 以管理员身份打开退货页。setup 里准备模拟后端的数据。 */
async function openDesk(page: Page, url: string, setup: (backend: FakeBackend) => void): Promise<FakeBackend> {
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  setup(backend);
  await backend.install(page);
  await page.goto(url);
  return backend;
}

const detail = (page: Page) => page.getByRole("region", { name: "详情" });
const notice = (page: Page) => page.getByLabel("操作提示");
const completeButton = (page: Page) => page.getByRole("button", { name: "完成退货" });
const rejectButton = (page: Page) => page.getByRole("button", { name: "拒绝退货" });

test("完成退货：备注选填；完成后说明商品放回库存，不说款项到账", async ({ page }) => {
  await openDesk(page, "/admin/returns?status=pending&id=1", (backend) => {
    backend.returns = [returnRequest({ id: 1 })];
  });

  await expect(detail(page)).toContainText("¥599.00");
  await expect(detail(page)).toContainText("完成后退货商品会放回库存");
  await completeButton(page).click();

  await expect(notice(page)).toContainText("退货申请 #1 已完成，退货商品已放回库存");
  await expect(detail(page)).toContainText("状态：已完成");
  await expect(detail(page)).toContainText("admin");
  await expect(page.getByText("暂无处理中的退货申请")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("到账");
});

test("拒绝退货：原因必填，拒绝后显示原因", async ({ page }) => {
  await openDesk(page, "/admin/returns?status=pending&id=1", (backend) => {
    backend.returns = [returnRequest({ id: 1 })];
  });

  await expect(rejectButton(page)).toBeDisabled();
  await page.getByLabel("拒绝原因（顾客能看到）").fill("商品已使用，不符合退货条件");
  await rejectButton(page).click();

  await expect(notice(page)).toContainText("退货申请 #1 已拒绝");
  await expect(detail(page)).toContainText("状态：已拒绝");
  await expect(detail(page)).toContainText("商品已使用，不符合退货条件");
});

test("顾客已经撤回后再点完成：409，显示已撤回，不能再提交", async ({ page }) => {
  const backend = await openDesk(page, "/admin/returns?status=pending&id=1", (backend) => {
    backend.returns = [returnRequest({ id: 1 })];
  });
  await expect(completeButton(page)).toBeVisible();
  Object.assign(backend.returns[0], { status: "cancelled", resolved_by: "carol" });

  await completeButton(page).click();

  await expect(notice(page)).toContainText("退货申请已经撤回，不能再处理");
  await expect(detail(page)).toContainText("状态：已撤回");
  await expect(completeButton(page)).toHaveCount(0);
  await expect(rejectButton(page)).toHaveCount(0);
});

test("完成和拒绝共用提交限制：一个在提交中，另一个不能点", async ({ page }) => {
  const backend = await openDesk(page, "/admin/returns?status=pending&id=1", (backend) => {
    backend.returns = [returnRequest({ id: 1 })];
  });
  await page.getByLabel("拒绝原因（顾客能看到）").fill("不符合条件");
  const slowComplete = backend.hold("POST", /^\/api\/admin\/returns\/1\/complete$/);

  await completeButton(page).click();
  await slowComplete.arrived;
  await expect(page.getByRole("button", { name: "提交中…" })).toHaveCount(2);
  await expect(page.getByRole("button", { name: "提交中…" }).first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "提交中…" }).last()).toBeDisabled();
  slowComplete.release();

  await expect(notice(page)).toContainText("已完成");
  expect(backend.requests.filter((request) => request.startsWith("POST"))).toEqual([
    "POST /api/admin/returns/1/complete",
  ]);
});

test("已撤回的历史记录没有处理说明时显示 —", async ({ page }) => {
  await openDesk(page, "/admin/returns?status=cancelled", (backend) => {
    backend.returns = [returnRequest({ id: 1, status: "cancelled", resolved_by: null, resolved_at: null })];
  });

  await page.getByRole("link", { name: /#1/ }).click();

  await expect(detail(page)).toContainText("状态：已撤回");
  const handler = detail(page).getByRole("definition").filter({ hasText: /^—$/ });
  await expect(handler).toHaveCount(3);
});
