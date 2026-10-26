"use client";

import { DeskFrame } from "@/components/admin/DeskFrame";
import { RecordList } from "@/components/admin/RecordList";
import { TicketDetailPanel } from "@/components/admin/TicketDetailPanel";
import { useAdminDesk, type DeskConfig } from "@/hooks/admin/useAdminDesk";
import {
  closeTicket,
  getTicket,
  listTickets,
  readStaleRecord,
  TICKET_STATUSES,
  type StaffTicket,
  type TicketDetail,
  type TicketStatus,
} from "@/lib/admin/api";
import { formatDateTime, TICKET_STATUS_LABELS } from "@/lib/admin/display";

const TICKET_DESK: DeskConfig<TicketStatus, StaffTicket, TicketDetail> = {
  path: "/admin/tickets",
  statuses: TICKET_STATUSES,
  loadList: listTickets,
  loadDetail: getTicket,
  describe: (detail) => ({
    statusLabel: TICKET_STATUS_LABELS[detail.ticket.status],
    waiting: detail.ticket.status === "open",
  }),
};

/** 工单页：左侧按状态列出工单，右侧显示选中工单的详情、来源会话的聊天记录和关闭表单。 */
export function TicketDesk() {
  const desk = useAdminDesk(TICKET_DESK);

  return (
    <DeskFrame
      forbidden={desk.forbidden}
      notice={desk.notice}
      submitting={desk.submitting}
      detail={desk.detail}
      emptyText="从左侧选择一张工单。"
      onDismissNotice={desk.dismissNotice}
      onRecheck={desk.recheck}
      onRefresh={desk.refresh}
      list={
        <RecordList
          title="工单"
          statuses={desk.statuses}
          statusLabels={TICKET_STATUS_LABELS}
          currentStatus={desk.status}
          selectedId={desk.selectedId}
          list={desk.list}
          hrefFor={desk.hrefFor}
          onRefresh={desk.refresh}
          renderItem={(ticket) => (
            <>
              <span className="flex justify-between text-gray-500">
                <span>
                  #{ticket.id} · {ticket.customer}
                </span>
                <span>{formatDateTime(ticket.created_at)}</span>
              </span>
              <span className="mt-1 line-clamp-2 block text-gray-900">{ticket.summary}</span>
            </>
          )}
        />
      }
      renderDetail={(detail, id) => (
        <TicketDetailPanel
          key={id}
          detail={detail}
          blocked={desk.isSubmitBlocked(id)}
          submitting={desk.submitting}
          onClose={(resolution) =>
            desk.submit({
              recordId: id,
              label: `工单 #${id}`,
              successText: "已关闭",
              run: async () => ({ ...detail, ticket: await closeTicket(id, resolution) }),
              fromConflict: (error) => {
                const current = readStaleRecord(error, "ticket");
                return current && { ...detail, ticket: current };
              },
            })
          }
        />
      )}
    />
  );
}
