import { expect, test, type Page } from "@playwright/test";

import { ADMIN, FakeBackend, knowledgeDocument } from "./fakeBackend";

const LIST = /^\/api\/admin\/knowledge\/documents$/;
const KEYBOARD_TITLE = "机械键盘 K8 说明书";

/** 以管理员身份打开知识库页。setup 里准备模拟后端的数据。 */
async function openKnowledge(page: Page, setup: (backend: FakeBackend) => void = () => {}): Promise<FakeBackend> {
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  setup(backend);
  await backend.install(page);
  await page.goto("/admin/knowledge");
  return backend;
}

const row = (page: Page, title: string) => page.getByRole("row", { name: title });
const notice = (page: Page) => page.getByLabel("操作提示");

function upload(page: Page, name: string, text: string): Promise<void> {
  return page
    .getByLabel("选择要上传的 Markdown 文档")
    .setInputFiles({ name, mimeType: "text/markdown", buffer: Buffer.from(text) });
}

test("上传文档：待处理 → 处理中 → 已就绪，状态自动刷新；然后停用", async ({ page }) => {
  const backend = await openKnowledge(page);
  await expect(page.getByRole("navigation", { name: "管理工作台" }).getByRole("link", { name: "知识库" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByText("还没有知识文档")).toBeVisible();

  await upload(page, "keyboard-k8.md", `# ${KEYBOARD_TITLE}\n\n## 轴体\n\n茶轴。\n`);

  await expect(notice(page)).toContainText(`已上传《${KEYBOARD_TITLE}》（第 1 版），正在后台建索引`);
  const keyboard = row(page, KEYBOARD_TITLE);
  await expect(keyboard).toContainText("待处理");
  await expect(keyboard).toContainText("正在建索引，暂时不会被检索");

  backend.knowledge[0].status = "processing";
  await expect(keyboard).toContainText("处理中");

  Object.assign(backend.knowledge[0], { status: "ready", passages: 1, indexed_at: "2026-10-26T02:01:00Z" });
  await expect(keyboard).toContainText("已就绪");
  await expect(keyboard).toContainText("可被检索");

  await keyboard.getByRole("button", { name: "停用" }).click();

  await expect(notice(page)).toContainText(`《${KEYBOARD_TITLE}》已停用`);
  await expect(keyboard).toContainText("已停用，不会被检索");
  await expect(keyboard.getByRole("button", { name: "启用" })).toBeVisible();
});

test("建索引失败：显示原因，可以重新索引", async ({ page }) => {
  const reason = "manual#全文：有 1200 个 token，超过向量模型的上限 1024，请拆成更短的小节";
  const backend = await openKnowledge(page, (backend) => {
    backend.knowledge = [
      knowledgeDocument({ id: 1, name: "manual", title: "长说明书", status: "failed", error: reason, passages: null }),
    ];
  });
  const manual = row(page, "长说明书");
  await expect(manual).toContainText("失败");
  await expect(manual).toContainText(reason);
  await expect(manual).toContainText("建索引失败，暂时不会被检索");

  await manual.getByRole("button", { name: "重新索引" }).click();

  await expect(notice(page)).toContainText("《长说明书》已安排重新建索引");
  await expect(manual).toContainText("待处理");
  expect(backend.count("POST", "/api/admin/knowledge/documents/1/reindex")).toBe(1);
});

test("不是 .md 的文件在浏览器里就拦下，不发请求", async ({ page }) => {
  const backend = await openKnowledge(page);
  await expect(page.getByText("还没有知识文档")).toBeVisible();

  await upload(page, "notes.txt", "随便写点");

  await expect(notice(page)).toContainText("上传“notes.txt”失败：只支持 .md 文件");
  expect(backend.requests.filter((request) => request.startsWith("POST"))).toEqual([]);
});

test("格式不对：显示后端返回的原因，列表不变", async ({ page }) => {
  await openKnowledge(page);
  await expect(page.getByText("还没有知识文档")).toBeVisible();

  await upload(page, "broken.md", "没有文档标题。\n");

  await expect(notice(page)).toContainText("上传“broken.md”失败：broken：第一行必须是“# 文档标题”");
  await expect(page.getByText("还没有知识文档")).toBeVisible();
});

test("没有文档在建索引时，不再定时刷新", async ({ page }) => {
  const backend = await openKnowledge(page, (backend) => {
    backend.knowledge = [knowledgeDocument({ id: 1, name: "faq", title: "常见问题" })];
  });
  await expect(row(page, "常见问题")).toContainText("已就绪");

  await page.waitForTimeout(2500);

  expect(backend.count("GET", "/api/admin/knowledge/documents")).toBe(1);
});

test("定时刷新发出的旧列表请求晚到，不会盖掉停用的结果", async ({ page }) => {
  const backend = await openKnowledge(page, (backend) => {
    backend.knowledge = [
      knowledgeDocument({ id: 1, name: "keyboard-k8", title: KEYBOARD_TITLE, status: "pending", passages: null }),
    ];
  });
  const keyboard = row(page, KEYBOARD_TITLE);
  await expect(keyboard).toContainText("待处理");
  // 下一次定时刷新：到达时按当时的数据（还是启用）算好回应，先不发出
  const slowPoll = backend.hold("GET", LIST);
  await slowPoll.arrived;

  await keyboard.getByRole("button", { name: "停用" }).click();
  await expect(keyboard).toContainText("已停用，不会被检索");
  slowPoll.release();

  await expect(keyboard.getByRole("button", { name: "启用" })).toBeVisible();
  await page.waitForTimeout(500);
  await expect(keyboard).toContainText("已停用，不会被检索");
});
