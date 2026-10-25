import { describe, expect, it } from "vitest";

import {
  actionTexts,
  canRespond,
  describeActionOutcome,
  formatCents,
  isActionView,
  type ActionStatus,
  type ActionView,
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

describe("确认卡片", () => {
  it("只有待确认的操作能点按钮", () => {
    for (const make of [returnRequest, returnCancellation, ticket]) {
      expect(canRespond(make("pending"))).toBe(true);
      for (const status of ["executed", "cancelled", "failed", "expired"] as const) {
        expect(canRespond(make(status))).toBe(false);
      }
    }
  });

  it("只有 executed 才说已经完成，并带上编号", () => {
    expect(describeActionOutcome(returnRequest("executed"))).toBe("已提交，退货单号 12");
    expect(describeActionOutcome(returnCancellation("executed"))).toBe("已撤回退货申请 12");
    expect(describeActionOutcome(ticket("executed"))).toBe("已创建工单 #3，等待人工处理");
    expect(describeActionOutcome(returnRequest("failed", { error: "已超过签收后 7 天的退货期" }))).toBe(
      "提交失败：已超过签收后 7 天的退货期。可以重新发起申请",
    );
    expect(describeActionOutcome(returnCancellation("failed", { error: "退货申请已经完成，不能再处理" }))).toBe(
      "撤回失败：退货申请已经完成，不能再处理",
    );
    expect(describeActionOutcome(returnRequest("expired"))).toContain("没有提交");
    expect(describeActionOutcome(ticket("cancelled"))).toBe("已取消，没有创建工单");
  });

  it("转人工成功只说创建了工单，不说人工客服已接入", () => {
    expect(describeActionOutcome(ticket("executed"))).not.toContain("接入");
  });

  it("撤回卡片不出现“取消”二字，免得和撤回退货混淆", () => {
    const texts = actionTexts(returnCancellation("pending"));
    expect([texts.title, texts.confirmLabel, texts.dismissLabel]).toEqual(["撤回退货申请", "确认撤回", "保留申请"]);
    expect(describeActionOutcome(returnCancellation("cancelled"))).toBe("已保留，退货申请没有撤回");
    for (const status of ["pending", "executed", "cancelled", "expired"] as const) {
      expect(describeActionOutcome(returnCancellation(status))).not.toContain("取消");
    }
  });

  it("能从 409 响应里认出操作状态", () => {
    expect(isActionView(ticket("cancelled"))).toBe(true);
    expect(isActionView({ detail: "出错了" })).toBe(false);
    expect(isActionView(null)).toBe(false);
  });
});
