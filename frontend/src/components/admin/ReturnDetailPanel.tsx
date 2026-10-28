import { useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { StaffReturn } from "@/lib/admin/api";
import { formatDateTime, RETURN_STATUS_LABELS } from "@/lib/admin/display";
import { formatCents } from "@/lib/actions";

/** 与后端 business/returns.py 的 NOTE_MAX_LENGTH 保持一致 */
const NOTE_MAX_LENGTH = 500;

/** 处理说明在不同结果下的名称 */
const RESOLUTION_NOTE_LABELS: Record<StaffReturn["status"], string> = {
  pending: "处理说明",
  completed: "完成备注",
  rejected: "拒绝原因",
  cancelled: "处理说明",
};

type Props = {
  request: StaffReturn;
  /** 正在提交，或者上次提交的结果还没确认。完成和拒绝共用这个限制 */
  blocked: boolean;
  submitting: boolean;
  /** 完成退货。成功返回 true；失败时表单内容保留 */
  onComplete: (note: string | null) => Promise<boolean>;
  onReject: (reason: string) => Promise<boolean>;
};

/** 一条退货申请的详情：基本信息，以及处理中时的“完成”和“拒绝”表单。 */
export function ReturnDetailPanel({ request, blocked, submitting, onComplete, onReject }: Props) {
  return (
    <article aria-labelledby="return-title" className="space-y-6">
      <header>
        <h2 id="return-title" className="text-lg font-medium">
          退货申请 #{request.id}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">状态：{RETURN_STATUS_LABELS[request.status]}</p>
      </header>

      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-xl border bg-card p-4 text-sm shadow-sm">
        <Field label="顾客">{request.customer}</Field>
        <Field label="订单">
          {request.order_id}（订单项 {request.order_item_id}）
        </Field>
        <Field label="商品">
          {request.product_name} × {request.quantity}
        </Field>
        <Field label="退款金额">¥{formatCents(request.refund_cents)}</Field>
        <Field label="申请原因">{request.reason}</Field>
        <Field label="签收时间">{formatDateTime(request.delivered_at)}</Field>
        <Field label="申请时间">{formatDateTime(request.created_at)}</Field>
        {request.status !== "pending" && (
          <>
            <Field label={RESOLUTION_NOTE_LABELS[request.status]}>{request.resolution_note ?? "—"}</Field>
            <Field label="处理人">{request.resolved_by ?? "—"}</Field>
            <Field label="处理时间">{formatDateTime(request.resolved_at)}</Field>
          </>
        )}
      </dl>

      {request.status === "pending" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <CompleteForm blocked={blocked} submitting={submitting} onComplete={onComplete} />
          <RejectForm blocked={blocked} submitting={submitting} onReject={onReject} />
        </div>
      )}
    </article>
  );
}

function CompleteForm({ blocked, submitting, onComplete }: Pick<Props, "blocked" | "submitting" | "onComplete">) {
  const [note, setNote] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    await onComplete(note.trim() || null);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 rounded-xl border bg-card p-4 shadow-sm">
      <label htmlFor="return-note" className="block text-sm font-medium">
        备注（选填，顾客能看到）
      </label>
      <textarea
        id="return-note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        maxLength={NOTE_MAX_LENGTH}
        rows={3}
        className="w-full rounded-lg border border-input bg-card px-3 py-2 text-sm outline-none transition-shadow duration-150 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25"
      />
      <p className="text-xs text-muted-foreground">完成后退货商品会放回库存。系统没有对接支付，退款需要另行处理。</p>
      <div className="flex justify-end">
        <Button type="submit" disabled={blocked} className="h-10 px-4 hover:bg-primary-hover">
          {submitting ? "提交中…" : "完成退货"}
        </Button>
      </div>
    </form>
  );
}

function RejectForm({ blocked, submitting, onReject }: Pick<Props, "blocked" | "submitting" | "onReject">) {
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!trimmed || blocked) return;
    await onReject(trimmed);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-2 rounded-xl border bg-card p-4 shadow-sm">
      <label htmlFor="return-reject-reason" className="block text-sm font-medium">
        拒绝原因（顾客能看到）
      </label>
      <textarea
        id="return-reject-reason"
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={NOTE_MAX_LENGTH}
        rows={3}
        required
        className="w-full rounded-lg border border-input bg-card px-3 py-2 text-sm outline-none transition-shadow duration-150 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/25"
      />
      <div className="flex items-center justify-end gap-3">
        {!trimmed && <span className="text-xs text-muted-foreground">填写拒绝原因后才能拒绝</span>}
        <Button type="submit" variant="destructive" disabled={!trimmed || blocked} className="h-10 px-4">
          {submitting ? "提交中…" : "拒绝退货"}
        </Button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="whitespace-pre-wrap [overflow-wrap:anywhere]">{children}</dd>
    </>
  );
}
