import { describe, expect, it } from "vitest";

import { readStaleRecord, type StaffReturn, type StaffTicket } from "@/lib/admin/api";
import { ApiError } from "@/lib/api";

const CLOSED_TICKET: StaffTicket = {
  id: 1,
  order_id: 6,
  summary: "顾客反馈包裹一直没有送到",
  status: "closed",
  resolution: "已联系物流补发",
  created_at: "2026-10-26T02:00:00Z",
  resolved_at: "2026-10-26T03:00:00Z",
  customer: "carol",
  conversation_id: 5,
  resolved_by: "admin",
};

const CANCELLED_RETURN: StaffReturn = {
  id: 4,
  order_id: 6,
  order_item_id: 9,
  product_name: "SoundPods 降噪耳机",
  quantity: 1,
  refund_cents: 59900,
  reason: "不想要了",
  status: "cancelled",
  resolution_note: null,
  created_at: "2026-10-26T02:00:00Z",
  resolved_at: "2026-10-26T03:00:00Z",
  customer: "carol",
  delivered_at: "2026-10-24T02:00:00Z",
  resolved_by: "carol",
};

function conflict(data: unknown): ApiError {
  return new ApiError(409, "状态已经变了", data);
}

describe("readStaleRecord", () => {
  it("409 时取出后端附带的工单当前状态", () => {
    const error = conflict({ detail: "工单已经关闭", ticket: CLOSED_TICKET });

    expect(readStaleRecord(error, "ticket")).toEqual(CLOSED_TICKET);
  });

  it("409 时取出后端附带的退货申请当前状态", () => {
    const error = conflict({ detail: "退货申请已经不在处理中", return: CANCELLED_RETURN });

    expect(readStaleRecord(error, "return")).toEqual(CANCELLED_RETURN);
  });

  it("附带的记录种类不对应时返回 null", () => {
    expect(readStaleRecord(conflict({ detail: "x", ticket: CLOSED_TICKET }), "return")).toBeNull();
  });

  it("不是 409 时返回 null", () => {
    const data = { detail: "x", ticket: CLOSED_TICKET };

    expect(readStaleRecord(new ApiError(422, "x", data), "ticket")).toBeNull();
    expect(readStaleRecord(new Error("x"), "ticket")).toBeNull();
    expect(readStaleRecord(new TypeError("Failed to fetch"), "ticket")).toBeNull();
  });

  it("附带的记录不完整或状态不认识时返回 null", () => {
    expect(readStaleRecord(conflict(null), "ticket")).toBeNull();
    expect(readStaleRecord(conflict({ detail: "x" }), "ticket")).toBeNull();
    expect(readStaleRecord(conflict({ detail: "x", ticket: { id: 1 } }), "ticket")).toBeNull();
    const unknownTicketStatus = { ...CLOSED_TICKET, status: "archived" };
    const unknownReturnStatus = { ...CANCELLED_RETURN, status: "open" };
    expect(readStaleRecord(conflict({ detail: "x", ticket: unknownTicketStatus }), "ticket")).toBeNull();
    expect(readStaleRecord(conflict({ detail: "x", return: unknownReturnStatus }), "return")).toBeNull();
  });
});
