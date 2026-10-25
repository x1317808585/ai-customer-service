/** 待确认操作（目前只有退货申请）的类型，以及确认卡片上显示的文字。 */

export type ActionStatus = "pending" | "executed" | "cancelled" | "failed" | "expired";

export type ReturnPreview = {
  order_id: number;
  order_item_id: number;
  product_name: string;
  quantity: number;
  refund_cents: number;
  reason: string;
};

export type ActionView = {
  id: number;
  kind: "return_request";
  status: ActionStatus;
  preview: ReturnPreview;
  result: { return_id: number } | null;
  error: string | null;
  expires_at: string;
};

/** 59900 → "599.00"。用整数运算，不经过小数。 */
export function formatCents(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

/** 只有“待确认”的操作还能点确认或取消。是否过期以服务器返回的状态为准。 */
export function canRespond(action: ActionView): boolean {
  return action.status === "pending";
}

/** 卡片底部的结果说明。只有 status 是 executed 才说“已提交”。 */
export function describeActionOutcome(action: ActionView): string {
  switch (action.status) {
    case "pending":
      return "请确认以下内容，确认后才会提交";
    case "executed":
      return `已提交，退货单号 ${action.result?.return_id ?? "未知"}`;
    case "cancelled":
      return "已取消，没有提交";
    case "expired":
      return "已过期，没有提交。如仍需退货，请重新发起";
    case "failed":
      return `提交失败：${action.error ?? "原因未知"}。可以重新发起申请`;
  }
}

/** 从任意数据里认出一个操作状态（比如 409 响应里附带的 action）。 */
export function isActionView(value: unknown): value is ActionView {
  return typeof value === "object" && value !== null && "id" in value && "status" in value && "preview" in value;
}
