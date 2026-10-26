import { describe, expect, it } from "vitest";

import { formatDateTime } from "@/lib/admin/display";

describe("formatDateTime", () => {
  it("按指定时区显示到分钟", () => {
    expect(formatDateTime("2026-10-26T02:26:00Z", "Australia/Sydney")).toBe("2026-10-26 13:26");
    expect(formatDateTime("2026-10-26T02:26:00Z", "UTC")).toBe("2026-10-26 02:26");
  });

  it("后端返回的微秒和时区偏移也能解析", () => {
    expect(formatDateTime("2026-10-26T02:26:59.123456+00:00", "UTC")).toBe("2026-10-26 02:26");
  });

  it("午夜显示为 00 点，不显示 24 点", () => {
    expect(formatDateTime("2026-10-25T13:00:00Z", "Australia/Sydney")).toBe("2026-10-26 00:00");
  });

  it("没有时间显示 —，无法解析时原样显示", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTime("不是时间")).toBe("不是时间");
  });
});
