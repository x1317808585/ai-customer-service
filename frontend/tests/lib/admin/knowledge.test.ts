import { describe, expect, it } from "vitest";

import type { KnowledgeDocument } from "@/lib/admin/api";
import { checkUploadFile, describeSearchability, MAX_UPLOAD_BYTES, needsPolling } from "@/lib/admin/knowledge";

function document(changes: Partial<KnowledgeDocument>): KnowledgeDocument {
  return {
    id: 1,
    name: "keyboard-k8",
    title: "机械键盘 K8 说明书",
    version: 1,
    status: "ready",
    error: null,
    enabled: true,
    passages: 5,
    indexed_at: "2026-11-07T00:00:00Z",
    updated_at: "2026-11-07T00:00:00Z",
    ...changes,
  };
}

describe("needsPolling", () => {
  it("有文档待处理或处理中时要继续刷新", () => {
    expect(needsPolling([document({}), document({ id: 2, status: "pending" })])).toBe(true);
    expect(needsPolling([document({ status: "processing" })])).toBe(true);
  });

  it("都已就绪或失败、或者没有文档时停止刷新", () => {
    expect(needsPolling([document({}), document({ id: 2, status: "failed", error: "出错了" })])).toBe(false);
    expect(needsPolling([])).toBe(false);
  });
});

describe("describeSearchability", () => {
  it("启用并且已就绪才能被检索", () => {
    expect(describeSearchability(document({}))).toEqual({ searchable: true, text: "可被检索" });
  });

  it("停用的文档不管什么状态都先说明已停用", () => {
    expect(describeSearchability(document({ enabled: false })).text).toBe("已停用，不会被检索");
    expect(describeSearchability(document({ enabled: false, status: "failed", error: "出错了" })).text).toBe(
      "已停用，不会被检索",
    );
  });

  it("建索引中和失败时都不能被检索", () => {
    expect(describeSearchability(document({ status: "pending", passages: null }))).toEqual({
      searchable: false,
      text: "正在建索引，暂时不会被检索",
    });
    expect(describeSearchability(document({ status: "failed", error: "出错了" }))).toEqual({
      searchable: false,
      text: "建索引失败，暂时不会被检索",
    });
  });
});

describe("checkUploadFile", () => {
  it("只收 .md 文件，扩展名不分大小写", () => {
    expect(checkUploadFile({ name: "faq.md", size: 10 })).toBeNull();
    expect(checkUploadFile({ name: "FAQ.MD", size: 10 })).toBeNull();
    expect(checkUploadFile({ name: "faq.txt", size: 10 })).toBe("只支持 .md 文件");
  });

  it("正好 1MB 可以，多一个字节不行", () => {
    expect(checkUploadFile({ name: "faq.md", size: MAX_UPLOAD_BYTES })).toBeNull();
    expect(checkUploadFile({ name: "faq.md", size: MAX_UPLOAD_BYTES + 1 })).toBe("文件不能超过 1MB");
  });
});
