import type { ReactNode } from "react";

import {
  actionTexts,
  canRespond,
  describeActionOutcome,
  formatCents,
  type ActionView,
  type ReturnCancellationPreview,
  type ReturnRequestPreview,
  type TicketPreview,
} from "@/lib/actions";

type Props = {
  action: ActionView;
  busy: boolean;
  onConfirm: (actionId: number) => void;
  onCancel: (actionId: number) => void;
};

/** 待确认操作的卡片。内容全部来自服务器计算的预览，不来自模型说的话。 */
export function ActionCard({ action, busy, onConfirm, onCancel }: Props) {
  const texts = actionTexts(action);
  const pending = canRespond(action);

  return (
    <div
      data-action-id={action.id}
      data-kind={action.kind}
      data-status={action.status}
      className="w-80 max-w-[75%] rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm"
    >
      <p className="font-medium text-gray-900">{texts.title}</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-gray-700">
        <ActionDetails action={action} />
      </dl>

      <p className={`mt-3 text-xs ${action.status === "executed" ? "text-green-700" : "text-gray-500"}`}>
        {describeActionOutcome(action)}
      </p>

      {pending && (
        <div className="mt-3 flex items-center justify-between gap-2">
          <span className="text-xs text-gray-400">
            {new Date(action.expires_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 前有效
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => onCancel(action.id)}
              className="rounded-lg border border-gray-300 px-3 py-1 text-gray-600 disabled:opacity-50"
            >
              {texts.dismissLabel}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onConfirm(action.id)}
              className="rounded-lg bg-blue-600 px-3 py-1 text-white disabled:opacity-50"
            >
              {busy ? "处理中…" : texts.confirmLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 按操作类型显示预览内容。 */
function ActionDetails({ action }: { action: ActionView }) {
  switch (action.kind) {
    case "return_request":
      return <ReturnRequestDetails preview={action.preview} />;
    case "return_cancellation":
      return <ReturnCancellationDetails preview={action.preview} />;
    case "ticket":
      return <TicketDetails preview={action.preview} />;
  }
}

function ReturnRequestDetails({ preview }: { preview: ReturnRequestPreview }) {
  return (
    <>
      <Row label="订单">
        {preview.order_id}（订单项 {preview.order_item_id}）
      </Row>
      <Row label="商品">
        {preview.product_name} × {preview.quantity}
      </Row>
      <Row label="退款">¥{formatCents(preview.refund_cents)}</Row>
      <Row label="原因">{preview.reason}</Row>
    </>
  );
}

function ReturnCancellationDetails({ preview }: { preview: ReturnCancellationPreview }) {
  return (
    <>
      <Row label="退货单">{preview.return_id}</Row>
      <Row label="订单">{preview.order_id}</Row>
      <Row label="商品">
        {preview.product_name} × {preview.quantity}
      </Row>
      <Row label="退款">¥{formatCents(preview.refund_cents)}</Row>
    </>
  );
}

function TicketDetails({ preview }: { preview: TicketPreview }) {
  return (
    <>
      {preview.order_id !== null && <Row label="订单">{preview.order_id}</Row>}
      <Row label="摘要">{preview.summary}</Row>
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className="whitespace-pre-wrap break-words">{children}</dd>
    </>
  );
}
