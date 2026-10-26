/** 管理工作台上显示的文字：状态名称、时间格式。 */

import type { ReturnStatus, TicketStatus } from "@/lib/admin/api";

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  open: "待处理",
  closed: "已关闭",
};

export const RETURN_STATUS_LABELS: Record<ReturnStatus, string> = {
  pending: "处理中",
  completed: "已完成",
  rejected: "已拒绝",
  cancelled: "已撤回",
};

/**
 * "2026-10-26T02:26:00Z" → "2026-10-26 13:26"。默认按浏览器所在的时区显示。
 * 没有时间显示 "—"；无法解析时原样显示，不隐藏数据。
 */
export function formatDateTime(value: string | null, timeZone?: string): string {
  if (value === null) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
}
