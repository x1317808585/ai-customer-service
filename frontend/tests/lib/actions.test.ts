import { describe, expect, it } from "vitest";

import {
  actionTexts,
  cardState,
  describeCard,
  formatCents,
  formatValidUntil,
  isActionView,
  type ActionStatus,
  type ActionView,
  type CardState,
} from "@/lib/actions";

const COMMON = { id: 1, error: null, expires_at: "2026-10-25T01:00:00Z" };

function returnRequest(status: ActionStatus, changes: Partial<ActionView> = {}): ActionView {
  return {
    ...COMMON,
    kind: "return_request",
    status,
    preview: {
      order_id: 5,
      order_item_id: 9,
      product_name: "SoundPods 降噪耳机",
      quantity: 1,
      refund_cents: 59900,
      reason: "坏了",
    },
    result: status === "executed" ? { return_id: 12 } : null,
    ...changes,
  } as ActionView;
}

function order(status: ActionStatus): ActionView {
  return {
    ...COMMON,
    kind: "order",
    status,
    preview: {
      lines: [{ product_id: 1, product_name: "SoundPods 降噪耳机", unit_price_cents: 59900, quantity: 1 }],
      total_cents: 59900,
    },
    result: status === "executed" ? { order_id: 21 } : null,
  } as ActionView;
}

function returnCancellation(status: ActionStatus, changes: Partial<ActionView> = {}): ActionView {
  return {
    ...COMMON,
    kind: "return_cancellation",
    status,
    preview: {
      return_id: 12,
      order_id: 5,
      order_item_id: 9,
      product_name: "SoundPods 降噪耳机",
      quantity: 1,
      refund_cents: 59900,
    },
    result: status === "executed" ? { return_id: 12 } : null,
    ...changes,
  } as ActionView;
}

function ticket(status: ActionStatus, changes: Partial<ActionView> = {}): ActionView {
  return {
    ...COMMON,
    kind: "ticket",
    status,
    preview: { order_id: 5, summary: "顾客反馈耳机左耳没声音" },
    result: status === "executed" ? { ticket_id: 3 } : null,
    ...changes,
  } as ActionView;
}

describe("formatCents", () => {
  it("把分换算成两位小数的元", () => {
    expect(formatCents(59900)).toBe("599.00");
    expect(formatCents(3905)).toBe("39.05");
    expect(formatCents(0)).toBe("0.00");
  });
});

/** 服务器状态对应的说明（卡片没有本地临时状态时显示的文字） */
const describe_ = (action: ActionView) => describeCard(action, action.status);

describe("cardState", () => {
  const BEFORE = new Date("2026-10-25T00:59:00Z");
  const AT_EXPIRY = new Date("2026-10-25T01:00:00Z");
  const AFTER = new Date("2026-10-25T01:01:00Z");
  const idle = { submitting: false, unverified: false };

  it.each<[string, ActionStatus, { submitting: boolean; unverified: boolean; now: Date }, CardState]>([
    ["待确认、没到期", "pending", { ...idle, now: BEFORE }, "pending"],
    ["待确认、恰好到期", "pending", { ...idle, now: AT_EXPIRY }, "expired"],
    ["服务器已提交，本地早就到期", "executed", { ...idle, now: AFTER }, "executed"],
    ["服务器已提交，本地还以为在提交", "executed", { ...idle, submitting: true, now: BEFORE }, "executed"],
    ["服务器失败，本地还以为结果待核实", "failed", { ...idle, unverified: true, now: AFTER }, "failed"],
    ["提交中优先于本地到期", "pending", { ...idle, submitting: true, now: AFTER }, "submitting"],
    ["结果待核实优先于本地到期", "pending", { ...idle, unverified: true, now: AFTER }, "unverified"],
  ])("%s", (_, status, local, expected) => {
    expect(cardState(returnRequest(status), local)).toBe(expected);
  });
});

describe("确认卡片的文字", () => {
  it("待确认只说确认后才提交，有效期按分钟显示", () => {
    expect(describe_(returnRequest("pending"))).toBe("确认后才会提交");
    expect(formatValidUntil("2026-10-25T01:05:00Z", "Australia/Sydney")).toBe("12:05 前有效");
  });

  it("只有 executed 才说已经完成，并带上编号", () => {
    expect(describe_(order("pending"))).toBe("确认后才会创建订单（模拟订单，不含支付）");
    expect(describe_(order("executed"))).toBe("模拟订单已创建，订单号 21");
    expect(describe_(returnRequest("executed"))).toBe("已提交，退货单号 12");
    expect(describe_(returnCancellation("executed"))).toBe("已撤回退货申请 12");
    expect(describe_(ticket("executed"))).toBe("已创建工单 #3，等待人工处理");
    expect(describe_(returnRequest("failed", { error: "已超过签收后 7 天的退货期" }))).toBe(
      "提交失败：已超过签收后 7 天的退货期。可以重新发起申请",
    );
    expect(describe_(returnCancellation("failed", { error: "退货申请已经完成，不能再处理" }))).toBe(
      "撤回失败：退货申请已经完成，不能再处理",
    );
    expect(describe_(returnRequest("expired"))).toContain("没有提交");
    expect(describe_(ticket("cancelled"))).toBe("已取消，没有创建工单");
  });

  it("转人工成功只说创建了工单，不说人工客服已接入", () => {
    expect(describe_(ticket("executed"))).not.toContain("接入");
  });

  it("撤回卡片不出现“取消”二字，免得和撤回退货混淆", () => {
    const texts = actionTexts(returnCancellation("pending"));
    expect([texts.title, texts.confirmLabel, texts.dismissLabel]).toEqual(["撤回退货申请", "确认撤回", "保留申请"]);
    expect(describe_(returnCancellation("cancelled"))).toBe("已保留，退货申请没有撤回");
    for (const status of ["pending", "executed", "cancelled", "expired"] as const) {
      expect(describe_(returnCancellation(status))).not.toContain("取消");
    }
  });

  it("能从 409 响应里认出操作状态", () => {
    expect(isActionView(ticket("cancelled"))).toBe(true);
    expect(isActionView({ detail: "出错了" })).toBe(false);
    expect(isActionView(null)).toBe(false);
  });
});
