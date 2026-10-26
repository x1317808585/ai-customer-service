import { useState, type FormEvent, type ReactNode } from "react";

import { TranscriptView } from "@/components/admin/TranscriptView";
import type { TicketDetail } from "@/lib/admin/api";
import { formatDateTime, TICKET_STATUS_LABELS } from "@/lib/admin/display";

/** 与后端 business/tickets.py 的 RESOLUTION_MAX_LENGTH 保持一致 */
const RESOLUTION_MAX_LENGTH = 2000;

type Props = {
  detail: TicketDetail;
  /** 正在提交，或者上次提交的结果还没确认 */
  blocked: boolean;
  submitting: boolean;
  /** 关闭工单。成功返回 true；失败时表单内容保留 */
  onClose: (resolution: string) => Promise<boolean>;
};

/** 一张工单的详情：基本信息、来源会话的聊天记录（只读）、关闭表单。 */
export function TicketDetailPanel({ detail, blocked, submitting, onClose }: Props) {
  const { ticket, transcript } = detail;

  return (
    <article aria-labelledby="ticket-title" className="space-y-6">
      <header>
        <h2 id="ticket-title" className="text-lg font-medium text-gray-900">
          工单 #{ticket.id}
        </h2>
        <p className="mt-1 text-sm text-gray-500">状态：{TICKET_STATUS_LABELS[ticket.status]}</p>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-xl bg-white p-4 text-sm shadow-sm">
        <Field label="顾客">{ticket.customer}</Field>
        <Field label="关联订单">{ticket.order_id ?? "—"}</Field>
        <Field label="创建时间">{formatDateTime(ticket.created_at)}</Field>
        <Field label="问题摘要">{ticket.summary}</Field>
        {ticket.status === "closed" && (
          <>
            <Field label="处理结果">{ticket.resolution ?? "—"}</Field>
            <Field label="处理人">{ticket.resolved_by ?? "—"}</Field>
            <Field label="处理时间">{formatDateTime(ticket.resolved_at)}</Field>
          </>
        )}
      </dl>

      {ticket.status === "open" && <CloseForm blocked={blocked} submitting={submitting} onClose={onClose} />}

      <section aria-labelledby="transcript-title">
        <h3 id="transcript-title" className="mb-3 text-sm font-medium text-gray-700">
          来源会话的聊天记录
        </h3>
        {ticket.conversation_id === null ? (
          <p className="text-sm text-gray-400">这张工单没有来源会话。</p>
        ) : (
          <TranscriptView messages={transcript} />
        )}
      </section>
    </article>
  );
}

function CloseForm({ blocked, submitting, onClose }: Omit<Props, "detail">) {
  const [resolution, setResolution] = useState("");
  const trimmed = resolution.trim();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || blocked) return;
    await onClose(trimmed);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 rounded-xl bg-white p-4 shadow-sm">
      <label htmlFor="ticket-resolution" className="block text-sm font-medium text-gray-700">
        处理结果（顾客能看到）
      </label>
      <textarea
        id="ticket-resolution"
        value={resolution}
        onChange={(event) => setResolution(event.target.value)}
        maxLength={RESOLUTION_MAX_LENGTH}
        rows={3}
        required
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
      />
      <div className="flex items-center justify-end gap-3">
        {!trimmed && <span className="text-xs text-gray-400">填写处理结果后才能关闭</span>}
        <button
          type="submit"
          disabled={!trimmed || blocked}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "提交中…" : "关闭工单"}
        </button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className="whitespace-pre-wrap break-words text-gray-900">{children}</dd>
    </>
  );
}
