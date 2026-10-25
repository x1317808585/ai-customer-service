import { canRespond, describeActionOutcome, formatCents, type ActionView } from "@/lib/actions";

type Props = {
  action: ActionView;
  busy: boolean;
  onConfirm: (actionId: number) => void;
  onCancel: (actionId: number) => void;
};

/** 退货申请的确认卡片。内容全部来自服务器计算的预览，不来自模型说的话。 */
export function ActionCard({ action, busy, onConfirm, onCancel }: Props) {
  const { preview } = action;
  const pending = canRespond(action);

  return (
    <div
      data-action-id={action.id}
      data-status={action.status}
      className="w-80 max-w-[75%] rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm shadow-sm"
    >
      <p className="font-medium text-gray-900">退货申请</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-gray-700">
        <dt className="text-gray-400">订单</dt>
        <dd>
          {preview.order_id}（订单项 {preview.order_item_id}）
        </dd>
        <dt className="text-gray-400">商品</dt>
        <dd>
          {preview.product_name} × {preview.quantity}
        </dd>
        <dt className="text-gray-400">退款</dt>
        <dd>¥{formatCents(preview.refund_cents)}</dd>
        <dt className="text-gray-400">原因</dt>
        <dd className="break-words">{preview.reason}</dd>
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
              取消
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onConfirm(action.id)}
              className="rounded-lg bg-blue-600 px-3 py-1 text-white disabled:opacity-50"
            >
              {busy ? "处理中…" : "确认提交"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
