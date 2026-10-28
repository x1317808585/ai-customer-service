import { describe, expect, it } from "vitest";

import { groupConversations } from "@/lib/customer/display";

const conversation = (id: number, created_at: string) => ({ id, title: `会话 ${id}`, created_at });

describe("groupConversations", () => {
  it("按所在时区的日期分成今天和更早，组内保持原来的顺序", () => {
    const now = new Date("2026-10-28T10:00:00Z"); // 悉尼 10-28 21:00
    const groups = groupConversations(
      [
        conversation(3, "2026-10-28T09:00:00Z"), // 悉尼 10-28 20:00
        conversation(2, "2026-10-27T13:30:00Z"), // 悉尼 10-28 00:30，跨过了午夜
        conversation(1, "2026-10-27T12:30:00Z"), // 悉尼 10-27 23:30
      ],
      now,
      "Australia/Sydney",
    );

    expect(groups.map((group) => [group.label, group.conversations.map((item) => item.id)])).toEqual([
      ["今天", [3, 2]],
      ["更早", [1]],
    ]);
  });

  it("没有今天的会话时，不返回空的“今天”组", () => {
    const groups = groupConversations([conversation(1, "2026-10-20T00:00:00Z")], new Date("2026-10-28T00:00:00Z"), "UTC");

    expect(groups.map((group) => group.label)).toEqual(["更早"]);
  });
});
