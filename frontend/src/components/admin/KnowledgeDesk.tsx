"use client";

import { useRef } from "react";
import { Upload, X } from "lucide-react";

import { AccessDenied } from "@/components/admin/AccessDenied";
import { Button } from "@/components/ui/button";
import {
  useKnowledgeDocuments,
  type DocumentsView,
  type KnowledgeNotice,
} from "@/hooks/admin/useKnowledgeDocuments";
import type { KnowledgeDocument, KnowledgeStatus } from "@/lib/admin/api";
import { formatDateTime } from "@/lib/admin/display";
import { describeSearchability, KNOWLEDGE_STATUS_LABELS } from "@/lib/admin/knowledge";

const STATUS_STYLES: Record<KnowledgeStatus, string> = {
  pending: "bg-status-pending-bg text-status-pending",
  processing: "bg-status-pending-bg text-status-pending",
  ready: "bg-status-success-bg text-status-success",
  failed: "bg-status-danger-bg text-status-danger",
};

const NOTICE_STYLES: Record<KnowledgeNotice["tone"], string> = {
  success: "bg-status-success-bg text-status-success",
  warning: "bg-status-pending-bg text-status-pending",
  error: "bg-status-danger-bg text-status-danger",
};

const COLUMNS = ["文档", "版本", "状态", "段落", "检索", "更新时间", "操作"];

/** 知识库页：文档列表、上传、启用和停用、重新索引。有文档在建索引时自动刷新状态。 */
export function KnowledgeDesk() {
  const desk = useKnowledgeDocuments();
  const fileInput = useRef<HTMLInputElement>(null);

  if (desk.forbidden) {
    return <AccessDenied />;
  }
  return (
    <div className="flex h-full flex-col">
      {desk.notice && <NoticeBar notice={desk.notice} onDismiss={desk.dismissNotice} />}
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl space-y-4">
          <header className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-lg font-medium">知识库</h1>
              <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                上传 Markdown 文档：第一行写“# 文档标题”，正文写在“## 小节标题”下面，不超过 1MB。
                同名文件会更新为新版本。启用并且已就绪的文档才会被检索。
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={desk.reload}
                className="rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                刷新
              </button>
              <input
                ref={fileInput}
                type="file"
                accept=".md,text/markdown"
                aria-label="选择要上传的 Markdown 文档"
                className="sr-only"
                disabled={desk.submitting}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = ""; // 同一个文件改完可以再选一次
                  if (file) desk.upload(file);
                }}
              />
              <Button
                disabled={desk.submitting}
                onClick={() => fileInput.current?.click()}
                className="h-9 px-3 hover:bg-primary-hover"
              >
                <Upload aria-hidden />
                {desk.submitting ? "提交中…" : "上传文档"}
              </Button>
            </div>
          </header>
          <DocumentTable
            view={desk.view}
            submitting={desk.submitting}
            onReload={desk.reload}
            onReindex={desk.reindex}
            onSetEnabled={desk.setEnabled}
          />
        </div>
      </div>
    </div>
  );
}

function DocumentTable({
  view,
  submitting,
  onReload,
  onReindex,
  onSetEnabled,
}: {
  view: DocumentsView;
  submitting: boolean;
  onReload: () => void;
  onReindex: (document: KnowledgeDocument) => void;
  onSetEnabled: (document: KnowledgeDocument, enabled: boolean) => void;
}) {
  if (view.kind === "loading") {
    return <p role="status" className="text-sm text-muted-foreground">加载中…</p>;
  }
  if (view.kind === "error") {
    return (
      <div role="alert" className="text-sm text-status-danger">
        <p>{view.message}</p>
        <button type="button" onClick={onReload} className="mt-2 font-medium underline underline-offset-4">
          重新加载
        </button>
      </div>
    );
  }
  if (view.documents.length === 0) {
    return (
      <p className="rounded-xl border bg-card p-6 text-sm text-muted-foreground shadow-sm">
        还没有知识文档。可以上传 .md 文件，或者在后端运行导入脚本。
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-xl border bg-card shadow-sm">
      <table className="w-full text-sm">
        <caption className="sr-only">知识文档</caption>
        <thead className="border-b text-left text-muted-foreground">
          <tr>
            {COLUMNS.map((column) => (
              <th key={column} scope="col" className="px-4 py-2 font-normal whitespace-nowrap">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {view.documents.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              submitting={submitting}
              onReindex={onReindex}
              onSetEnabled={onSetEnabled}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DocumentRow({
  document,
  submitting,
  onReindex,
  onSetEnabled,
}: {
  document: KnowledgeDocument;
  submitting: boolean;
  onReindex: (document: KnowledgeDocument) => void;
  onSetEnabled: (document: KnowledgeDocument, enabled: boolean) => void;
}) {
  const searchability = describeSearchability(document);
  return (
    <tr aria-label={document.title} className="border-b align-top last:border-b-0">
      <td className="px-4 py-3">
        <span className="font-medium">{document.title}</span>
        <span className="mt-0.5 block text-xs text-muted-foreground">{document.name}.md</span>
      </td>
      <td className="px-4 py-3 whitespace-nowrap">第 {document.version} 版</td>
      <td className="px-4 py-3">
        <span className={`inline-block rounded-md px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[document.status]}`}>
          {KNOWLEDGE_STATUS_LABELS[document.status]}
        </span>
        {document.status === "failed" && document.error && (
          <p className="mt-1 max-w-xs text-xs text-status-danger [overflow-wrap:anywhere]">{document.error}</p>
        )}
      </td>
      <td className="px-4 py-3">{document.passages ?? "—"}</td>
      <td className={`px-4 py-3 ${searchability.searchable ? "text-status-success" : "text-muted-foreground"}`}>
        {searchability.text}
      </td>
      <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">{formatDateTime(document.updated_at)}</td>
      <td className="px-4 py-3">
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={submitting} onClick={() => onReindex(document)}>
            重新索引
          </Button>
          <Button
            variant={document.enabled ? "destructive" : "outline"}
            size="sm"
            disabled={submitting}
            onClick={() => onSetEnabled(document, !document.enabled)}
          >
            {document.enabled ? "停用" : "启用"}
          </Button>
        </div>
      </td>
    </tr>
  );
}

function NoticeBar({ notice, onDismiss }: { notice: KnowledgeNotice; onDismiss: () => void }) {
  return (
    <div
      role={notice.tone === "error" ? "alert" : "status"}
      aria-label="操作提示"
      className={`flex items-center gap-4 px-6 py-2 text-sm ${NOTICE_STYLES[notice.tone]}`}
    >
      <p className="flex-1">{notice.text}</p>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="关闭提示"
        className="flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-black/5 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <X aria-hidden className="size-4" />
      </button>
    </div>
  );
}
