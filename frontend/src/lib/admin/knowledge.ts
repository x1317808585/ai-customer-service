/** 知识库页面的显示规则：状态名称、能不能被检索、要不要继续刷新、上传前的检查。 */

import type { KnowledgeDocument, KnowledgeStatus } from "@/lib/admin/api";

/** 上传文件的大小上限，和后端 app/api/admin/knowledge.py 里的 MAX_UPLOAD_BYTES 一致。 */
export const MAX_UPLOAD_BYTES = 1_000_000;

/** 有文档还在建索引时，每隔这么久重新读取一次列表。 */
export const POLL_INTERVAL_MS = 2000;

export const KNOWLEDGE_STATUS_LABELS: Record<KnowledgeStatus, string> = {
  pending: "待处理",
  processing: "处理中",
  ready: "已就绪",
  failed: "失败",
};

/** 还在建索引：待处理或处理中。 */
export function isIndexing(document: KnowledgeDocument): boolean {
  return document.status === "pending" || document.status === "processing";
}

/** 有文档还在建索引时，要定时刷新列表；都处理完了就停。 */
export function needsPolling(documents: KnowledgeDocument[]): boolean {
  return documents.some(isIndexing);
}

/** 这篇文档现在能不能被检索，以及为什么。和后端一致：启用并且已就绪才能被检索。 */
export function describeSearchability(document: KnowledgeDocument): { searchable: boolean; text: string } {
  if (!document.enabled) return { searchable: false, text: "已停用，不会被检索" };
  switch (document.status) {
    case "ready":
      return { searchable: true, text: "可被检索" };
    case "failed":
      return { searchable: false, text: "建索引失败，暂时不会被检索" };
    default:
      return { searchable: false, text: "正在建索引，暂时不会被检索" };
  }
}

/** 上传前先在浏览器里检查一遍（后端还会再检查）：不合格返回原因，合格返回 null。 */
export function checkUploadFile(file: { name: string; size: number }): string | null {
  if (!file.name.toLowerCase().endsWith(".md")) return "只支持 .md 文件";
  if (file.size > MAX_UPLOAD_BYTES) return "文件不能超过 1MB";
  return null;
}
