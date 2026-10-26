"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { classifyFailure, parseRecordId, parseStatus } from "@/lib/admin/desk";
import { describeError } from "@/lib/api";

/**
 * 一个管理页面（工单、退货申请）的数据来源。函数要定义在组件外面，保持引用不变。
 * 这里只描述“怎么读数据”，不包含任何处理规则：写操作由详情组件通过 submit 传进来。
 */
export type DeskConfig<Status extends string, Item extends { id: number }, Detail> = {
  /** 页面地址，比如 "/admin/tickets" */
  path: string;
  /** 可以筛选的状态，第一个是默认值 */
  statuses: readonly Status[];
  loadList: (status: Status, signal: AbortSignal) => Promise<Item[]>;
  loadDetail: (id: number, signal: AbortSignal) => Promise<Detail>;
  /** 记录当前的状态名称，以及是否还在等待处理。写请求结果不明、重新查询之后用来写提示。 */
  describe: (detail: Detail) => { statusLabel: string; waiting: boolean };
};

export type ListView<Item> =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: Item[] };

export type DetailView<Detail> =
  | { kind: "none" }
  | { kind: "invalid" }
  | { kind: "loading" }
  | { kind: "notFound" }
  | { kind: "error"; message: string }
  | { kind: "ready"; id: number; data: Detail };

export type Notice = {
  tone: "success" | "warning" | "error";
  text: string;
  /** 写请求的结果不明确时，提供“重新查询”这条记录 */
  recheck?: { recordId: number; label: string };
};

export type Submission<Detail> = {
  recordId: number;
  /** 提示里怎么称呼这条记录，比如“工单 #3” */
  label: string;
  /** 执行写请求，返回处理后的详情 */
  run: () => Promise<Detail>;
  /** 409 时从错误里取出记录的当前状态，合成详情；取不到返回 null */
  fromConflict: (error: unknown) => Detail | null;
  /** 成功后的提示，比如“已关闭” */
  successText: string;
};

type ListResult<Item> = { kind: "ok"; items: Item[] } | { kind: "error"; message: string };
type DetailResult<Detail> = { kind: "ok"; data: Detail } | { kind: "notFound" } | { kind: "error"; message: string };

/**
 * 管理页面的请求生命周期：按地址栏读列表和详情、执行写请求、丢弃过期的响应、按类处理失败。
 *
 * - 地址栏是筛选条件和选中记录的唯一来源，切换都通过链接完成（见 hrefFor）。
 * - 切换记录、切换筛选、写请求有了结果、收到 403 时，之前发出还没返回的读请求一律作废，
 *   旧响应不会覆盖新状态。
 * - 同一时间只允许一个写请求；写请求的结果只更新它自己那条记录的详情。
 * - 写请求没收到响应时不自动重试，先重新查询记录；查询也失败时，确认之前不能再提交这条记录。
 */
export function useAdminDesk<Status extends string, Item extends { id: number }, Detail>(
  config: DeskConfig<Status, Item, Detail>,
) {
  const { path, statuses, loadList, loadDetail, describe } = config;
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawStatus = searchParams.get("status");
  const status = parseStatus(rawStatus, statuses) ?? statuses[0];
  const selection = parseRecordId(searchParams.get("id"));
  const selectedId = selection.kind === "selected" ? selection.id : null;

  const [list, setList] = useState<{ status: Status; result: ListResult<Item> } | null>(null);
  const [detail, setDetail] = useState<{ id: number; result: DetailResult<Detail> } | null>(null);
  // 版本号加一会重新读取，同时让还没返回的旧读取作废（effect 的清理函数会丢弃它们的结果）
  const [listVersion, setListVersion] = useState(0);
  const [detailVersion, setDetailVersion] = useState(0);
  const [forbidden, setForbidden] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [unconfirmedId, setUnconfirmedId] = useState<number | null>(null);
  const submittingRef = useRef(false);

  const dismissNotice = useCallback(() => setNotice(null), []);

  const hrefFor = useCallback(
    (nextStatus: Status, id?: number) => `${path}?status=${nextStatus}${id === undefined ? "" : `&id=${id}`}`,
    [path],
  );

  // 地址栏里没有 status 或者不合法：换成规范的地址，不新增浏览器历史
  useEffect(() => {
    if (rawStatus !== status) router.replace(hrefFor(status, selectedId ?? undefined));
  }, [rawStatus, status, selectedId, router, hrefFor]);

  /** 401 跳登录页，403 清空页面数据并显示没有权限。处理了返回 true。 */
  const handleAccessFailure = useCallback((error: unknown): boolean => {
    const kind = classifyFailure(error);
    if (kind === "unauthorized") {
      window.location.replace("/login");
      return true;
    }
    if (kind === "forbidden") {
      setForbidden(true);
      setList(null);
      setDetail(null);
      setNotice(null);
      return true;
    }
    return false;
  }, []);

  useEffect(() => {
    if (forbidden) return;
    const controller = new AbortController();
    let active = true;
    loadList(status, controller.signal).then(
      (items) => {
        if (active) setList({ status, result: { kind: "ok", items } });
      },
      (error: unknown) => {
        if (!active || handleAccessFailure(error)) return;
        setList({ status, result: { kind: "error", message: describeError(error) } });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [status, listVersion, forbidden, loadList, handleAccessFailure]);

  useEffect(() => {
    if (forbidden || selectedId === null) return;
    const id = selectedId;
    const controller = new AbortController();
    let active = true;
    loadDetail(id, controller.signal).then(
      (data) => {
        if (active) setDetail({ id, result: { kind: "ok", data } });
      },
      (error: unknown) => {
        if (!active || handleAccessFailure(error)) return;
        const notFound = classifyFailure(error) === "notFound";
        setDetail({ id, result: notFound ? { kind: "notFound" } : { kind: "error", message: describeError(error) } });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedId, detailVersion, forbidden, loadDetail, handleAccessFailure]);

  /** 用写请求或重新查询的结果更新这条记录的详情。已经切到别的记录并且那条已经显示出来时，不覆盖。 */
  const applyDetail = useCallback((recordId: number, data: Detail) => {
    setDetail((previous) =>
      previous && previous.id !== recordId ? previous : { id: recordId, result: { kind: "ok", data } },
    );
  }, []);

  /** 重新读取列表和详情，同时让还没返回的旧读取作废。 */
  const refresh = useCallback(() => {
    setListVersion((version) => version + 1);
    setDetailVersion((version) => version + 1);
  }, []);

  /** 写请求没收到明确结果时，重新查询这条记录，按查到的状态提示。 */
  const confirmOutcome = useCallback(
    async (recordId: number, label: string) => {
      try {
        const data = await loadDetail(recordId, new AbortController().signal);
        applyDetail(recordId, data);
        setUnconfirmedId((current) => (current === recordId ? null : current));
        const { statusLabel, waiting } = describe(data);
        setNotice(
          waiting
            ? {
                tone: "warning",
                text:
                  `${label}：刚才的请求没有收到明确结果。当前仍显示“${statusLabel}”，可能还在处理中，也可能没有成功。` +
                  "可以稍后重新查询，或者重新提交。",
                recheck: { recordId, label },
              }
            : {
                tone: "warning",
                text:
                  `${label}：刚才的请求没有收到明确结果。当前状态是“${statusLabel}”，` +
                  "但不能确定是不是这次提交完成的。",
              },
        );
      } catch (error) {
        if (handleAccessFailure(error)) return;
        if (classifyFailure(error) === "notFound") {
          setUnconfirmedId((current) => (current === recordId ? null : current));
          setNotice({ tone: "error", text: `${label}不存在。` });
          return;
        }
        setUnconfirmedId(recordId);
        setNotice({
          tone: "error",
          text: `${label}：结果暂时无法确认，请稍后重新查询。确认之前不能再次提交。`,
          recheck: { recordId, label },
        });
      }
    },
    [loadDetail, describe, applyDetail, handleAccessFailure],
  );

  /** 执行一个写请求。成功返回 true；失败时按类型处理，表单内容由调用方保留。 */
  const submit = useCallback(
    async (submission: Submission<Detail>): Promise<boolean> => {
      if (submittingRef.current) return false;
      submittingRef.current = true;
      setSubmitting(true);
      setNotice(null);
      const { recordId, label } = submission;
      try {
        applyDetail(recordId, await submission.run());
        setNotice({ tone: "success", text: `${label} ${submission.successText}` });
        return true;
      } catch (error) {
        if (handleAccessFailure(error)) return false;
        switch (classifyFailure(error)) {
          case "stale": {
            const current = submission.fromConflict(error);
            if (current !== null) applyDetail(recordId, current);
            setNotice({ tone: "warning", text: `${label}：${describeError(error)}。页面已更新为最新状态。` });
            return false;
          }
          case "notFound":
            setNotice({ tone: "error", text: `${label}不存在。` });
            return false;
          case "rejected":
            setNotice({ tone: "error", text: `${label}：${describeError(error)}` });
            return false;
          default:
            await confirmOutcome(recordId, label);
            return false;
        }
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
        refresh();
      }
    },
    [applyDetail, handleAccessFailure, confirmOutcome, refresh],
  );

  /** 提示里的“重新查询”。 */
  const recheck = useCallback(
    async (recordId: number, label: string) => {
      if (submittingRef.current) return;
      submittingRef.current = true;
      setSubmitting(true);
      try {
        await confirmOutcome(recordId, label);
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
        refresh();
      }
    },
    [confirmOutcome, refresh],
  );

  const listView: ListView<Item> =
    list === null || list.status !== status
      ? { kind: "loading" }
      : list.result.kind === "ok"
        ? { kind: "ready", items: list.result.items }
        : { kind: "error", message: list.result.message };

  let detailView: DetailView<Detail>;
  if (selection.kind === "none") detailView = { kind: "none" };
  else if (selection.kind === "invalid") detailView = { kind: "invalid" };
  else if (detail === null || detail.id !== selection.id) detailView = { kind: "loading" };
  else if (detail.result.kind === "ok") detailView = { kind: "ready", id: detail.id, data: detail.result.data };
  else detailView = detail.result;

  return {
    status,
    statuses,
    selectedId,
    hrefFor,
    list: listView,
    detail: detailView,
    forbidden,
    notice,
    dismissNotice,
    submitting,
    /** 正在提交，或者这条记录上次的结果还没确认时，不能提交 */
    isSubmitBlocked: (recordId: number) => submitting || unconfirmedId === recordId,
    submit,
    recheck,
    refresh,
  };
}
