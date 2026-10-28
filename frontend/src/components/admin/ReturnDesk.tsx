"use client";

import { DeskFrame } from "@/components/admin/DeskFrame";
import { RecordList } from "@/components/admin/RecordList";
import { ReturnDetailPanel } from "@/components/admin/ReturnDetailPanel";
import { useAdminDesk, type DeskConfig, type Submission } from "@/hooks/admin/useAdminDesk";
import {
  completeReturn,
  getReturn,
  listReturns,
  readStaleRecord,
  rejectReturn,
  RETURN_STATUSES,
  type ReturnStatus,
  type StaffReturn,
} from "@/lib/admin/api";
import { formatDateTime, RETURN_STATUS_LABELS } from "@/lib/admin/display";
import { formatCents } from "@/lib/actions";

const RETURN_DESK: DeskConfig<ReturnStatus, StaffReturn, StaffReturn> = {
  path: "/admin/returns",
  statuses: RETURN_STATUSES,
  loadList: listReturns,
  loadDetail: getReturn,
  describe: (request) => ({
    statusLabel: RETURN_STATUS_LABELS[request.status],
    waiting: request.status === "pending",
  }),
};

/** 退货页：左侧按状态列出退货申请，右侧显示选中申请的详情，以及完成、拒绝表单。 */
export function ReturnDesk() {
  const desk = useAdminDesk(RETURN_DESK);

  /** 完成和拒绝的共同部分：409 时用后端附带的当前状态更新详情 */
  const submission = (
    id: number,
    successText: string,
    run: () => Promise<StaffReturn>,
  ): Submission<StaffReturn> => ({
    recordId: id,
    label: `退货申请 #${id}`,
    successText,
    run,
    fromConflict: (error) => readStaleRecord(error, "return"),
  });

  return (
    <DeskFrame
      forbidden={desk.forbidden}
      notice={desk.notice}
      submitting={desk.submitting}
      detail={desk.detail}
      emptyText="从左侧选择一条退货申请。"
      onDismissNotice={desk.dismissNotice}
      onRecheck={desk.recheck}
      onRefresh={desk.refresh}
      list={
        <RecordList
          title="退货申请"
          statuses={desk.statuses}
          statusLabels={RETURN_STATUS_LABELS}
          currentStatus={desk.status}
          selectedId={desk.selectedId}
          list={desk.list}
          hrefFor={desk.hrefFor}
          onRefresh={desk.refresh}
          renderItem={(request) => (
            <>
              <span className="flex justify-between text-muted-foreground">
                <span>
                  #{request.id} · {request.customer}
                </span>
                <span>{formatDateTime(request.created_at)}</span>
              </span>
              <span className="mt-1 block">
                {request.product_name} × {request.quantity} · ¥{formatCents(request.refund_cents)}
              </span>
            </>
          )}
        />
      }
      renderDetail={(request, id) => (
        <ReturnDetailPanel
          key={id}
          request={request}
          blocked={desk.isSubmitBlocked(id)}
          submitting={desk.submitting}
          onComplete={(note) =>
            desk.submit(submission(id, "已完成，退货商品已放回库存", () => completeReturn(id, note)))
          }
          onReject={(reason) => desk.submit(submission(id, "已拒绝", () => rejectReturn(id, reason)))}
        />
      )}
    />
  );
}
