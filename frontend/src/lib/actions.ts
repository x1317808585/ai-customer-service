/** 待确认操作（退货申请、撤回退货申请、转人工）的类型，以及确认卡片上显示的文字。 */

export type ActionStatus = "pending" | "executed" | "cancelled" | "failed" | "expired";

export type ReturnRequestPreview = {
  order_id: number;
  order_item_id: number;
  product_name: string;
  quantity: number;
  refund_cents: number;
  reason: string;
};

export type ReturnCancellationPreview = {
  return_id: number;
  order_id: number;
  order_item_id: number;
  product_name: string;
  quantity: number;
  refund_cents: number;
};

export type TicketPreview = {
  order_id: number | null;
  summary: string;
};

type ActionCommon = {
  id: number;
  status: ActionStatus;
  error: string | null;
  expires_at: string;
};

/** 按 kind 区分的三种操作：TypeScript 能据此检查每种操作的预览和结果字段。 */
export type ActionView =
  | (ActionCommon & {
      kind: "return_request";
      preview: ReturnRequestPreview;
      result: { return_id: number } | null;
    })
  | (ActionCommon & {
      kind: "return_cancellation";
      preview: ReturnCancellationPreview;
      result: { return_id: number } | null;
    })
  | (ActionCommon & {
      kind: "ticket";
      preview: TicketPreview;
      result: { ticket_id: number } | null;
    });

export type ActionKind = ActionView["kind"];

type ActionTexts = {
  title: string;
  confirmLabel: string;
  dismissLabel: string;
  pending: string;
  cancelled: string;
  expired: string;
  failed: (error: string) => string;
};

/** 每种操作的文字。撤回卡片不用“取消”二字，免得和“撤回退货申请”混淆。 */
const ACTION_TEXTS: Record<ActionKind, ActionTexts> = {
  return_request: {
    title: "退货申请",
    confirmLabel: "确认提交",
    dismissLabel: "取消",
    pending: "请确认以下内容，确认后才会提交",
    cancelled: "已取消，没有提交",
    expired: "已过期，没有提交。如仍需退货，请重新发起",
    failed: (error) => `提交失败：${error}。可以重新发起申请`,
  },
  return_cancellation: {
    title: "撤回退货申请",
    confirmLabel: "确认撤回",
    dismissLabel: "保留申请",
    pending: "确认后会撤回这条退货申请",
    cancelled: "已保留，退货申请没有撤回",
    expired: "已过期，退货申请没有撤回",
    failed: (error) => `撤回失败：${error}`,
  },
  ticket: {
    title: "转人工",
    confirmLabel: "确认转人工",
    dismissLabel: "先不转",
    pending: "确认后会创建工单，交给人工客服处理",
    cancelled: "已取消，没有创建工单",
    expired: "已过期，没有创建工单。如仍需人工处理，请重新发起",
    failed: (error) => `创建失败：${error}。可以重新发起`,
  },
};

export function actionTexts(action: ActionView): ActionTexts {
  return ACTION_TEXTS[action.kind];
}

/** 59900 → "599.00"。用整数运算，不经过小数。 */
export function formatCents(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

/** 只有“待确认”的操作还能点确认或取消。是否过期以服务器返回的状态为准。 */
export function canRespond(action: ActionView): boolean {
  return action.status === "pending";
}

/** 卡片底部的结果说明。只有 status 是 executed 才说已经完成。 */
export function describeActionOutcome(action: ActionView): string {
  const texts = actionTexts(action);
  switch (action.status) {
    case "pending":
      return texts.pending;
    case "executed":
      return describeExecuted(action);
    case "cancelled":
      return texts.cancelled;
    case "expired":
      return texts.expired;
    case "failed":
      return texts.failed(action.error ?? "原因未知");
  }
}

function describeExecuted(action: ActionView): string {
  switch (action.kind) {
    case "return_request":
      return `已提交，退货单号 ${action.result?.return_id ?? "未知"}`;
    case "return_cancellation":
      return `已撤回退货申请 ${action.result?.return_id ?? "未知"}`;
    case "ticket":
      return `已创建工单 #${action.result?.ticket_id ?? "未知"}，等待人工处理`;
  }
}

/** 从任意数据里认出一个操作状态（比如 409 响应里附带的 action）。 */
export function isActionView(value: unknown): value is ActionView {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    "kind" in value &&
    "status" in value &&
    "preview" in value
  );
}
