/** 顾客聊天页上的显示规则。 */

import type { Conversation } from "@/lib/customer/api";

export type ConversationGroup = {
  label: "今天" | "更早";
  conversations: Conversation[];
};

/**
 * 会话按创建日期分成“今天”和“更早”两组，保持原来的顺序，空的组不返回。
 * 后端没有“最近活动时间”，所以按创建时间分组。日期按浏览器所在的时区计算。
 */
export function groupConversations(
  conversations: Conversation[],
  now: Date,
  timeZone?: string,
): ConversationGroup[] {
  const today = dayOf(now, timeZone);
  const groups: ConversationGroup[] = [
    { label: "今天", conversations: [] },
    { label: "更早", conversations: [] },
  ];
  for (const conversation of conversations) {
    const isToday = dayOf(new Date(conversation.created_at), timeZone) === today;
    groups[isToday ? 0 : 1].conversations.push(conversation);
  }
  return groups.filter((group) => group.conversations.length > 0);
}

/** 某个时刻在指定时区里是哪一天，比如 "2026-10-28"。 */
function dayOf(date: Date, timeZone?: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    date,
  );
}
