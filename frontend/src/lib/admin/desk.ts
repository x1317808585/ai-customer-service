/** 管理工作台的纯逻辑：解析地址栏参数、给请求失败归类、判断列表是否到了返回上限。 */

import { ApiError } from "@/lib/api";

/** 管理列表一次最多返回的条数。对应后端 business/tickets.py 和 business/returns.py 里的 STAFF_LIST_LIMIT。 */
export const STAFF_LIST_LIMIT = 50;

/** 地址栏里的 status：合法就原样返回，否则返回 null（由页面换成默认值）。 */
export function parseStatus<S extends string>(value: string | null, allowed: readonly S[]): S | null {
  return allowed.find((status) => status === value) ?? null;
}

/** 地址栏里的 id：没有选中、编号无效（不是正整数），或者选中了某条记录。 */
export type RecordSelection = { kind: "none" } | { kind: "invalid" } | { kind: "selected"; id: number };

export function parseRecordId(value: string | null): RecordSelection {
  if (value === null || value === "") return { kind: "none" };
  if (!/^[1-9]\d*$/.test(value)) return { kind: "invalid" };
  const id = Number(value);
  return Number.isSafeInteger(id) ? { kind: "selected", id } : { kind: "invalid" };
}

/** 返回的条数到了上限。只能说明“可能还有更早的记录”，不能断言一定还有。 */
export function reachedListLimit(count: number): boolean {
  return count >= STAFF_LIST_LIMIT;
}

/**
 * 请求失败的几类，页面按类处理。
 *
 * - stale：409，记录状态已经变了，后端附带了当前状态
 * - rejected：后端明确拒绝（比如 422 输入有误），记录没有变化，可以修改后再提交
 * - unknown：网络错误或服务器出错（5xx）。写请求可能已经被执行了，要重新查询，不能当作失败
 */
export type FailureKind = "unauthorized" | "forbidden" | "notFound" | "stale" | "rejected" | "unknown";

export function classifyFailure(error: unknown): FailureKind {
  if (!(error instanceof ApiError) || error.status >= 500) return "unknown";
  switch (error.status) {
    case 401:
      return "unauthorized";
    case 403:
      return "forbidden";
    case 404:
      return "notFound";
    case 409:
      return "stale";
    default:
      return "rejected";
  }
}
