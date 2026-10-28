import type { ReactNode } from "react";
import { X } from "lucide-react";

import { AccessDenied } from "@/components/admin/AccessDenied";
import type { DetailView, Notice } from "@/hooks/admin/useAdminDesk";

type Props<Detail> = {
  forbidden: boolean;
  notice: Notice | null;
  submitting: boolean;
  detail: DetailView<Detail>;
  /** 左侧的列表 */
  list: ReactNode;
  /** 没有选中记录时，右侧显示的提示 */
  emptyText: string;
  renderDetail: (data: Detail, id: number) => ReactNode;
  onDismissNotice: () => void;
  onRecheck: (recordId: number, label: string) => void;
  onRefresh: () => void;
};

/** 工单页和退货页共用的布局：顶部提示、左侧列表、右侧详情（含加载中、不存在、出错等情况）。 */
export function DeskFrame<Detail>({
  forbidden,
  notice,
  submitting,
  detail,
  list,
  emptyText,
  renderDetail,
  onDismissNotice,
  onRecheck,
  onRefresh,
}: Props<Detail>) {
  if (forbidden) {
    return <AccessDenied />;
  }
  return (
    <div className="flex h-full flex-col">
      {notice && (
        <NoticeBar notice={notice} submitting={submitting} onDismiss={onDismissNotice} onRecheck={onRecheck} />
      )}
      <div className="flex min-h-0 flex-1">
        <div className="flex w-96 shrink-0 flex-col border-r bg-card">{list}</div>
        <section aria-label="详情" className="min-w-0 flex-1 overflow-y-auto p-6">
          <DetailState detail={detail} emptyText={emptyText} renderDetail={renderDetail} onRefresh={onRefresh} />
        </section>
      </div>
    </div>
  );
}

const NOTICE_STYLES: Record<Notice["tone"], string> = {
  success: "bg-status-success-bg text-status-success",
  warning: "bg-status-pending-bg text-status-pending",
  error: "bg-status-danger-bg text-status-danger",
};

function NoticeBar({
  notice,
  submitting,
  onDismiss,
  onRecheck,
}: {
  notice: Notice;
  submitting: boolean;
  onDismiss: () => void;
  onRecheck: (recordId: number, label: string) => void;
}) {
  const { recheck } = notice;
  return (
    <div
      role={notice.tone === "error" ? "alert" : "status"}
      aria-label="操作提示"
      className={`flex items-center gap-4 px-6 py-2 text-sm ${NOTICE_STYLES[notice.tone]}`}
    >
      <p className="flex-1">{notice.text}</p>
      {recheck && (
        <button
          type="button"
          disabled={submitting}
          onClick={() => onRecheck(recheck.recordId, recheck.label)}
          className="shrink-0 font-medium underline underline-offset-4 disabled:opacity-50"
        >
          重新查询
        </button>
      )}
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

function DetailState<Detail>({
  detail,
  emptyText,
  renderDetail,
  onRefresh,
}: {
  detail: DetailView<Detail>;
  emptyText: string;
  renderDetail: (data: Detail, id: number) => ReactNode;
  onRefresh: () => void;
}) {
  switch (detail.kind) {
    case "none":
      return <p className="text-sm text-muted-foreground">{emptyText}</p>;
    case "invalid":
      return <p className="text-sm text-muted-foreground">编号无效：地址里的 id 必须是正整数。</p>;
    case "loading":
      return <p role="status" className="text-sm text-muted-foreground">加载中…</p>;
    case "notFound":
      return <p className="text-sm text-muted-foreground">记录不存在，请检查编号。</p>;
    case "error":
      return (
        <div role="alert" className="text-sm text-status-danger">
          <p>{detail.message}</p>
          <button type="button" onClick={onRefresh} className="mt-2 font-medium underline underline-offset-4">
            重新加载
          </button>
        </div>
      );
    case "ready":
      return renderDetail(detail.data, detail.id);
  }
}
