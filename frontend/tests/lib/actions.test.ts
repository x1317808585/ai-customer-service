import { describe, expect, it } from "vitest";

import { canRespond, describeActionOutcome, formatCents, isActionView, type ActionView } from "@/lib/actions";

function action(changes: Partial<ActionView>): ActionView {
  return {
    id: 1,
    kind: "return_request",
    status: "pending",
    preview: {
      order_id: 5,
      order_item_id: 9,
      product_name: "SoundPods 降噪耳机",
      quantity: 1,
      refund_cents: 59900,
      reason: "坏了",
    },
    result: null,
    error: null,
    expires_at: "2026-10-25T01:00:00Z",
    ...changes,
  };
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
    expect(canRespond(action({ status: "pending" }))).toBe(true);
    for (const status of ["executed", "cancelled", "failed", "expired"] as const) {
      expect(canRespond(action({ status }))).toBe(false);
    }
  });

  it("只有 executed 才显示已提交，并带上退货单号", () => {
    expect(describeActionOutcome(action({ status: "executed", result: { return_id: 12 } }))).toBe(
      "已提交，退货单号 12",
    );
    expect(describeActionOutcome(action({ status: "failed", error: "已超过签收后 7 天的退货期" }))).toBe(
      "提交失败：已超过签收后 7 天的退货期。可以重新发起申请",
    );
    expect(describeActionOutcome(action({ status: "expired" }))).toContain("没有提交");
    expect(describeActionOutcome(action({ status: "cancelled" }))).toContain("没有提交");
  });

  it("能从 409 响应里认出操作状态", () => {
    expect(isActionView(action({}))).toBe(true);
    expect(isActionView({ detail: "出错了" })).toBe(false);
    expect(isActionView(null)).toBe(false);
  });
});
