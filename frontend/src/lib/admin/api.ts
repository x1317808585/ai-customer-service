/**
 * 管理端用到的接口（/api/admin）：类型和请求。通用的请求方法和错误类型在 @/lib/api。
 *
 * 处理人由后端取自当前登录的管理员，请求里不传。时间字段都是 ISO 字符串，金额单位是分。
 */

import { ApiError, jsonBody, request, type ChatMessage } from "@/lib/api";

export const TICKET_STATUSES = ["open", "closed"] as const;
export type TicketStatus = (typeof TICKET_STATUSES)[number];

export const RETURN_STATUSES = ["pending", "completed", "rejected", "cancelled"] as const;
export type ReturnStatus = (typeof RETURN_STATUSES)[number];

/** 管理员看到的工单：比顾客多了顾客名、来源会话和处理人。 */
export type StaffTicket = {
  id: number;
  order_id: number | null;
  summary: string;
  status: TicketStatus;
  resolution: string | null;
  created_at: string;
  resolved_at: string | null;
  customer: string;
  conversation_id: number | null;
  /** 处理人的用户名 */
  resolved_by: string | null;
};

export type TicketDetail = {
  ticket: StaffTicket;
  /** 来源会话的全部消息（只读）；工单没有来源会话时为空 */
  transcript: ChatMessage[];
};

/** 管理员看到的退货申请：比顾客多了顾客名、签收时间和处理人。 */
export type StaffReturn = {
  id: number;
  order_id: number;
  order_item_id: number;
  product_name: string;
  quantity: number;
  refund_cents: number;
  reason: string;
  status: ReturnStatus;
  /** 完成时的备注或拒绝的原因，顾客能看到 */
  resolution_note: string | null;
  created_at: string;
  resolved_at: string | null;
  customer: string;
  delivered_at: string | null;
  /** 处理人的用户名；阶段 11 之前结束的历史记录为空 */
  resolved_by: string | null;
};

export async function listTickets(status: TicketStatus, signal?: AbortSignal): Promise<StaffTicket[]> {
  return (await request(`/api/admin/tickets?status=${status}`, { signal })).json();
}

export async function getTicket(ticketId: number, signal?: AbortSignal): Promise<TicketDetail> {
  return (await request(`/api/admin/tickets/${ticketId}`, { signal })).json();
}

export async function closeTicket(ticketId: number, resolution: string): Promise<StaffTicket> {
  return (await request(`/api/admin/tickets/${ticketId}/close`, jsonBody("POST", { resolution }))).json();
}

export async function listReturns(status: ReturnStatus, signal?: AbortSignal): Promise<StaffReturn[]> {
  return (await request(`/api/admin/returns?status=${status}`, { signal })).json();
}

export async function getReturn(returnId: number, signal?: AbortSignal): Promise<StaffReturn> {
  return (await request(`/api/admin/returns/${returnId}`, { signal })).json();
}

/** 完成退货：状态变为已完成，商品放回库存。不代表退款已经到账（项目没有对接支付）。 */
export async function completeReturn(returnId: number, note: string | null): Promise<StaffReturn> {
  return (await request(`/api/admin/returns/${returnId}/complete`, jsonBody("POST", { note }))).json();
}

export async function rejectReturn(returnId: number, reason: string): Promise<StaffReturn> {
  return (await request(`/api/admin/returns/${returnId}/reject`, jsonBody("POST", { reason }))).json();
}

type StaleRecords = { ticket: StaffTicket; return: StaffReturn };

const KNOWN_STATUSES: Record<keyof StaleRecords, readonly string[]> = {
  ticket: TICKET_STATUSES,
  return: RETURN_STATUSES,
};

/**
 * 409 时后端附带的记录当前状态：{"detail": ..., "ticket": {...}} 或 {"detail": ..., "return": {...}}。
 * 不是 409，或者没有附带可用的记录时返回 null。
 */
export function readStaleRecord<K extends keyof StaleRecords>(error: unknown, key: K): StaleRecords[K] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  if (typeof error.data !== "object" || error.data === null) return null;
  const record: unknown = (error.data as Record<string, unknown>)[key];
  return hasKnownStatus(record, KNOWN_STATUSES[key]) ? (record as StaleRecords[K]) : null;
}

function hasKnownStatus(value: unknown, statuses: readonly string[]): boolean {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    typeof value.id === "number" &&
    "status" in value &&
    typeof value.status === "string" &&
    statuses.includes(value.status)
  );
}
