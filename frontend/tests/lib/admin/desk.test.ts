import { describe, expect, it } from "vitest";

import { RETURN_STATUSES, TICKET_STATUSES } from "@/lib/admin/api";
import { classifyFailure, parseRecordId, parseStatus, reachedListLimit, STAFF_LIST_LIMIT } from "@/lib/admin/desk";
import { ApiError } from "@/lib/api";

describe("parseStatus", () => {
  it("合法的状态原样返回", () => {
    expect(parseStatus("closed", TICKET_STATUSES)).toBe("closed");
    expect(parseStatus("cancelled", RETURN_STATUSES)).toBe("cancelled");
  });

  it("没有、拼错或属于另一种记录的状态返回 null", () => {
    expect(parseStatus(null, TICKET_STATUSES)).toBeNull();
    expect(parseStatus("", TICKET_STATUSES)).toBeNull();
    expect(parseStatus("Closed", TICKET_STATUSES)).toBeNull();
    expect(parseStatus("pending", TICKET_STATUSES)).toBeNull();
    expect(parseStatus("open", RETURN_STATUSES)).toBeNull();
  });
});

describe("parseRecordId", () => {
  it("没有 id 时表示没有选中", () => {
    expect(parseRecordId(null)).toEqual({ kind: "none" });
    expect(parseRecordId("")).toEqual({ kind: "none" });
  });

  it("正整数表示选中了这条记录", () => {
    expect(parseRecordId("3")).toEqual({ kind: "selected", id: 3 });
    expect(parseRecordId("123")).toEqual({ kind: "selected", id: 123 });
  });

  it.each(["0", "-1", "1.5", "abc", "03", " 3", "3 ", "1e3", "9007199254740993"])("%s 是无效编号", (value) => {
    expect(parseRecordId(value)).toEqual({ kind: "invalid" });
  });
});

describe("reachedListLimit", () => {
  it("返回的条数到了后端上限才提示", () => {
    expect(reachedListLimit(0)).toBe(false);
    expect(reachedListLimit(STAFF_LIST_LIMIT - 1)).toBe(false);
    expect(reachedListLimit(STAFF_LIST_LIMIT)).toBe(true);
  });
});

describe("classifyFailure", () => {
  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "notFound"],
    [409, "stale"],
    [422, "rejected"],
    [400, "rejected"],
    [500, "unknown"],
    [502, "unknown"],
  ] as const)("状态码 %i 归为 %s", (status, kind) => {
    expect(classifyFailure(new ApiError(status, "x"))).toBe(kind);
  });

  it("网络错误（请求可能已经送达）归为 unknown", () => {
    expect(classifyFailure(new TypeError("Failed to fetch"))).toBe("unknown");
    expect(classifyFailure(new Error("x"))).toBe("unknown");
  });
});
