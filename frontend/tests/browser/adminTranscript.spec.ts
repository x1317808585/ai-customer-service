import { expect, test } from "@playwright/test";

import { ADMIN, FakeBackend, ticket } from "./fakeBackend";

test("聊天记录里助手回复的引用：显示编号，展开后是保存时的出处和原文", async ({ page }) => {
  const citation = {
    source_id: "return-policy#退货期限",
    document_title: "退货政策",
    section: "退货期限",
    content: "签收后 7 天内可以申请退货。",
  };
  const backend = new FakeBackend();
  backend.currentUser = ADMIN;
  backend.tickets = [ticket({ id: 1, conversation_id: 5 })];
  backend.transcripts.set(1, [
    { id: 1, role: "user", content: "买了一周还能退吗", created_at: "2026-10-26T01:00:00Z", action: null, citations: [] },
    {
      id: 2,
      role: "assistant",
      content: `根据《退货政策》“退货期限”：「${citation.content}」[来源：${citation.source_id}]。`,
      created_at: "2026-10-26T01:00:05Z",
      action: null,
      citations: [citation],
    },
  ]);
  await backend.install(page);

  await page.goto("/admin/tickets?status=open&id=1");

  const transcript = page.getByRole("list", { name: "来源会话的聊天记录" });
  await expect(transcript).toContainText(`「${citation.content}」[1]`);
  await expect(transcript).not.toContainText("[来源：");

  await transcript.getByText("参考资料（1）").click();
  const references = transcript.getByRole("list", { name: "参考资料" });
  await expect(references).toContainText("[1] 《退货政策》· 退货期限");
  await expect(references).toContainText(citation.content);
});
