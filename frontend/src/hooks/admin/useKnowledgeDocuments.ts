"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  listKnowledgeDocuments,
  reindexKnowledgeDocument,
  setKnowledgeDocumentEnabled,
  uploadKnowledgeDocument,
  type KnowledgeDocument,
} from "@/lib/admin/api";
import { classifyFailure } from "@/lib/admin/desk";
import { checkUploadFile, needsPolling, POLL_INTERVAL_MS } from "@/lib/admin/knowledge";
import { describeError } from "@/lib/api";

export type DocumentsView =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; documents: KnowledgeDocument[] };

export type KnowledgeNotice = { tone: "success" | "warning" | "error"; text: string };

/**
 * 知识库页面的数据：读取文档列表，有文档在建索引时每 2 秒刷新一次；上传、重新索引、启用和停用。
 *
 * - 同一时间只允许一个写操作。
 * - 写操作有了结果以后，之前发出还没返回的列表请求（包括定时刷新发出的）一律作废，
 *   旧列表不会盖掉刚做的修改；然后按服务器上的实际状态重新读取。
 * - 写操作没收到响应时，结果不确定：提示管理员核对，并重新读取列表，不自动重试。
 */
export function useKnowledgeDocuments() {
  const [view, setView] = useState<DocumentsView>({ kind: "loading" });
  // 版本号加一会重新读取列表，同时让还没返回的旧读取作废（effect 的清理函数会丢弃它们的结果）
  const [version, setVersion] = useState(0);
  const [forbidden, setForbidden] = useState(false);
  const [notice, setNotice] = useState<KnowledgeNotice | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);

  const reload = useCallback(() => setVersion((current) => current + 1), []);
  const dismissNotice = useCallback(() => setNotice(null), []);

  /** 401 跳登录页，403 显示没有权限。处理了返回 true。 */
  const handleAccessFailure = useCallback((error: unknown): boolean => {
    const kind = classifyFailure(error);
    if (kind === "unauthorized") {
      window.location.replace("/login");
      return true;
    }
    if (kind === "forbidden") {
      setForbidden(true);
      setNotice(null);
      return true;
    }
    return false;
  }, []);

  useEffect(() => {
    if (forbidden) return;
    const controller = new AbortController();
    let active = true;
    listKnowledgeDocuments(controller.signal).then(
      (documents) => {
        if (active) setView({ kind: "ready", documents });
      },
      (error: unknown) => {
        if (!active || handleAccessFailure(error)) return;
        setView({ kind: "error", message: describeError(error) });
      },
    );
    return () => {
      active = false;
      controller.abort();
    };
  }, [version, forbidden, handleAccessFailure]);

  // 有文档在建索引时，每次读到列表后，隔一会儿再读一次
  const polling = !forbidden && view.kind === "ready" && needsPolling(view.documents);
  useEffect(() => {
    if (!polling) return;
    const timer = window.setTimeout(reload, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [polling, view, reload]);

  const run = useCallback(
    async (
      label: string,
      operation: () => Promise<KnowledgeDocument>,
      successText: (document: KnowledgeDocument) => string,
    ) => {
      if (submittingRef.current) return;
      submittingRef.current = true;
      setSubmitting(true);
      setNotice(null);
      try {
        const document = await operation();
        setView((current) =>
          current.kind === "ready" ? { kind: "ready", documents: upsert(current.documents, document) } : current,
        );
        setNotice({ tone: "success", text: successText(document) });
      } catch (error) {
        if (!handleAccessFailure(error)) {
          setNotice(
            classifyFailure(error) === "unknown"
              ? {
                  tone: "warning",
                  text: `${label}没有收到服务器的确认，结果不确定。已重新读取列表，请核对后再决定是否重试。`,
                }
              : { tone: "error", text: `${label}失败：${describeError(error)}` },
          );
        }
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
        // 作废还没返回的旧列表请求，按服务器上的实际状态重新读取
        reload();
      }
    },
    [handleAccessFailure, reload],
  );

  const upload = useCallback(
    (file: File) => {
      const label = `上传“${file.name}”`;
      const problem = checkUploadFile(file);
      if (problem) {
        setNotice({ tone: "error", text: `${label}失败：${problem}` });
        return;
      }
      void run(
        label,
        () => uploadKnowledgeDocument(file),
        (document) => `已上传《${document.title}》（第 ${document.version} 版），正在后台建索引`,
      );
    },
    [run],
  );

  const reindex = useCallback(
    (document: KnowledgeDocument) =>
      void run(
        `重新索引《${document.title}》`,
        () => reindexKnowledgeDocument(document.id),
        (updated) => `《${updated.title}》已安排重新建索引`,
      ),
    [run],
  );

  const setEnabled = useCallback(
    (document: KnowledgeDocument, enabled: boolean) =>
      void run(
        `${enabled ? "启用" : "停用"}《${document.title}》`,
        () => setKnowledgeDocumentEnabled(document.id, enabled),
        (updated) => `《${updated.title}》已${updated.enabled ? "启用" : "停用"}`,
      ),
    [run],
  );

  return { view, forbidden, notice, submitting, dismissNotice, reload, upload, reindex, setEnabled };
}

/** 用写操作返回的文档替换列表里的那一篇（新文档就加进去），按名称排序。 */
function upsert(documents: KnowledgeDocument[], document: KnowledgeDocument): KnowledgeDocument[] {
  const others = documents.filter((item) => item.id !== document.id);
  return [...others, document].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}
