"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isActionView, type ActionView } from "@/lib/actions";
import { ApiError, describeError, isUnauthorized, type ChatMessage } from "@/lib/api";
import {
  cancelAction,
  confirmAction,
  createConversation,
  getAction,
  listConversations,
  listMessages,
  sendMessage,
  type Conversation,
} from "@/lib/customer/api";
import { applyReplyEvent, type ToolProgress } from "@/lib/customer/replyDraft";
import { settleReply, type Notice, type ReplyEnding } from "@/lib/customer/replyOutcome";
import { readSseEvents } from "@/lib/customer/sse";

export type DisplayMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
  /** 生成过程中的工具调用进度，只在生成期间显示，不会保存。 */
  tools: ToolProgress[];
  /** 这条消息是确认卡片时，对应操作的当前状态 */
  action: ActionView | null;
  /** 回复中途结束、又读不到会话记录时，留在页面上的半截回复。它没有保存在服务器上。 */
  unfinished?: true;
};

/** 聊天页面的全部状态和操作。组件只负责显示，以及把用户操作转交给这里。 */
export function useChat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [replying, setReplying] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyActionIds, setBusyActionIds] = useState<ReadonlySet<number>>(new Set());
  /** 确认或取消之后，连查询都失败了、不知道结果的操作 */
  const [unverifiedActionIds, setUnverifiedActionIds] = useState<ReadonlySet<number>>(new Set());
  /** 正在重新查询结果的操作 */
  const [checkingActionIds, setCheckingActionIds] = useState<ReadonlySet<number>>(new Set());

  const activeIdRef = useRef<number | null>(null);
  const latestListRequest = useRef(0);
  const historyRequest = useRef<AbortController | null>(null);
  const replyRequest = useRef<AbortController | null>(null);
  const stopRequested = useRef(false);

  /** 登录过期时整页跳到登录页（清掉页面上的全部数据）；其他错误显示在页面顶部。 */
  const handleError = useCallback((error: unknown) => {
    if (isUnauthorized(error)) {
      window.location.replace("/login");
      return;
    }
    setNotice({ level: "error", text: describeError(error), canReload: false });
  }, []);

  /** 重新读取会话列表。只采用最新一次请求的结果，旧请求晚到时直接丢弃。 */
  const refreshConversations = useCallback(async (): Promise<Conversation[] | null> => {
    const requestId = ++latestListRequest.current;
    try {
      const list = await listConversations();
      if (requestId !== latestListRequest.current) return null;
      setConversations(list);
      return list;
    } catch (error) {
      handleError(error);
      return null;
    }
  }, [handleError]);

  /**
   * 读取会话记录，成功时用服务器上的记录整体替换页面上的消息，返回是否成功。
   * 失败时页面上已有的消息和卡片保持不动；quiet 为 false 时显示“读取失败”的提示。
   * 新的读取会取消还没返回的旧读取，避免旧结果覆盖新会话。
   */
  const loadHistory = useCallback(
    async (conversationId: number, { quiet = false } = {}): Promise<boolean> => {
      historyRequest.current?.abort();
      const controller = new AbortController();
      historyRequest.current = controller;
      try {
        const history = await listMessages(conversationId, controller.signal);
        setMessages(history.map(toDisplayMessage));
        setUnverifiedActionIds(new Set()); // 读到的就是服务器上的最新状态
        return true;
      } catch (error) {
        if (controller.signal.aborted) return false;
        if (isUnauthorized(error)) handleError(error);
        else if (!quiet) {
          setNotice({ level: "error", text: `会话记录读取失败：${describeError(error)}`, canReload: true });
        }
        return false;
      }
    },
    [handleError],
  );

  /** 切换到某个会话（或 null 表示还没有会话），清空当前显示的内容。 */
  const activate = useCallback((conversationId: number | null) => {
    historyRequest.current?.abort();
    activeIdRef.current = conversationId;
    setActiveId(conversationId);
    setMessages([]);
    setNotice(null);
    setUnverifiedActionIds(new Set());
  }, []);

  useEffect(() => {
    void (async () => {
      const list = await refreshConversations();
      if (list && list.length > 0 && activeIdRef.current === null) {
        activate(list[0].id);
        void loadHistory(list[0].id);
      }
    })();
  }, [refreshConversations, activate, loadHistory]);

  const selectConversation = useCallback(
    (conversationId: number) => {
      if (replying || conversationId === activeId) return;
      activate(conversationId);
      void loadHistory(conversationId);
    },
    [replying, activeId, activate, loadHistory],
  );

  /** 重新读取当前会话的记录和会话列表，不发送任何东西。失败时页面上已有的内容保持不动。 */
  const reloadConversation = useCallback(async () => {
    if (activeId === null || replying) return;
    setNotice(null);
    void refreshConversations();
    await loadHistory(activeId);
  }, [activeId, replying, refreshConversations, loadHistory]);

  /** 新建会话并切换过去。失败时返回 null。请求返回前禁止再次新建，避免连点创建出多个会话。 */
  const openNewConversation = useCallback(async (): Promise<number | null> => {
    setCreating(true);
    try {
      const conversation = await createConversation();
      activate(conversation.id);
      void refreshConversations();
      return conversation.id;
    } catch (error) {
      handleError(error);
      return null;
    } finally {
      setCreating(false);
    }
  }, [activate, refreshConversations, handleError]);

  const startNewConversation = useCallback(async () => {
    if (!replying && !creating) await openNewConversation();
  }, [replying, creating, openNewConversation]);

  /** 发送消息。返回 false 表示输入框应恢复原文（服务器明确拒绝，或者不确定消息有没有发出去）。 */
  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (replying || creating) return false;
      setNotice(null);

      const conversationId = activeId ?? (await openNewConversation());
      if (conversationId === null) return false;

      const now = Date.now();
      const questionKey = `question-${now}`;
      const replyKey = `reply-${now}`;
      setMessages((previous) => [
        ...previous,
        { key: questionKey, role: "user", content: text, streaming: false, tools: [], action: null },
        { key: replyKey, role: "assistant", content: "", streaming: true, tools: [], action: null },
      ]);
      const updateReply = (change: (message: DisplayMessage) => DisplayMessage) =>
        setMessages((previous) =>
          previous.map((message) => (message.key === replyKey ? change(message) : message)),
        );

      const controller = new AbortController();
      replyRequest.current = controller;
      stopRequested.current = false;
      setReplying(true);

      let accepted = false;
      let done = false;
      let failure: { detail: string | null } | null = null;
      try {
        const body = await sendMessage(conversationId, text, controller.signal);
        for await (const event of readSseEvents(body)) {
          if (event.event === "message.accepted") {
            accepted = true;
            void refreshConversations();
          } else if (event.event === "message.done") {
            done = true;
            updateReply((message) => ({ ...message, streaming: false }));
          } else if (event.event === "error") {
            failure = { detail: (JSON.parse(event.data) as { detail?: string }).detail ?? null };
          } else if (event.event === "action.proposed") {
            // 确认卡片已经保存在服务器上了：作为一条独立消息，插在正在生成的回复前面
            const { message_id: messageId, action } = JSON.parse(event.data) as {
              message_id: number;
              action: ActionView;
            };
            const card: DisplayMessage = {
              key: `message-${messageId}`,
              role: "assistant",
              content: "",
              streaming: false,
              tools: [],
              action,
            };
            setMessages((previous) => {
              const replyIndex = previous.findIndex((message) => message.key === replyKey);
              return [...previous.slice(0, replyIndex), card, ...previous.slice(replyIndex)];
            });
          } else {
            // 正文片段、撤回草稿、工具进度，统一交给 applyReplyEvent 处理
            updateReply((message) => {
              const draft = applyReplyEvent({ text: message.content, tools: message.tools }, event);
              return { ...message, content: draft.text, tools: draft.tools };
            });
          }
        }
      } catch (error) {
        if (isUnauthorized(error)) {
          handleError(error);
          return false;
        }
        if (error instanceof ApiError && error.status < 500) {
          // 服务器明确拒绝了，什么都没保存
          setMessages((previous) => previous.filter((message) => message.key !== questionKey && message.key !== replyKey));
          setNotice(settleReply({ kind: "rejected", detail: error.message }, true).notice);
          return false;
        }
        // 断网、5xx、用户点了停止：下面按情况收尾
      } finally {
        replyRequest.current = null;
        setReplying(false);
      }
      if (done) return true;

      const ending: ReplyEnding = !accepted
        ? { kind: "unsent" }
        : failure
          ? { kind: "failed", detail: failure.detail }
          : stopRequested.current
            ? { kind: "stopped" }
            : { kind: "interrupted" };
      updateReply((message) => ({ ...message, streaming: false, tools: [] }));
      const loaded = await loadHistory(conversationId, { quiet: true });
      if (!loaded) {
        // 读不到记录：页面上已有的内容都留着，半截回复标明“未完成，未保存”，空的直接去掉
        setMessages((previous) =>
          previous.flatMap((message) => {
            if (message.key !== replyKey) return [message];
            return message.content ? [{ ...message, unfinished: true as const }] : [];
          }),
        );
      }
      const settled = settleReply(ending, loaded);
      setNotice(settled.notice);
      return !settled.restoreInput;
    },
    [replying, creating, activeId, openNewConversation, refreshConversations, loadHistory, handleError],
  );

  /** 用最新的操作状态替换页面上对应的卡片。拿到了服务器的状态，就不再是“结果待核实”。 */
  const showAction = useCallback((action: ActionView) => {
    setMessages((previous) =>
      previous.map((message) => (message.action?.id === action.id ? { ...message, action } : message)),
    );
    setUnverifiedActionIds((previous) => without(previous, action.id));
  }, []);

  /**
   * 点击卡片上的“确认提交”或“取消”。卡片完全按服务器返回的 status 显示。
   * 网络断开或服务器出错时，操作可能已经执行了，所以重新查询它的当前状态，而不是直接当作失败；
   * 查询也失败时，卡片显示“结果待核实”，只能重新查询，不能再确认或取消。
   */
  const respondToAction = useCallback(
    async (actionId: number, decision: "confirm" | "cancel") => {
      if (busyActionIds.has(actionId)) return;
      setBusyActionIds((previous) => new Set(previous).add(actionId));
      setNotice(null);
      try {
        showAction(await (decision === "confirm" ? confirmAction(actionId) : cancelAction(actionId)));
      } catch (error) {
        if (error instanceof ApiError && error.status < 500) {
          const data = error.data as { action?: unknown } | null;
          if (error.status === 409 && isActionView(data?.action)) showAction(data.action);
          handleError(error);
        } else {
          try {
            showAction(await getAction(actionId));
            setNotice({
              level: "info",
              text: "刚才的请求没有收到明确结果，已重新查询并显示操作的当前状态。",
              canReload: false,
            });
          } catch (queryError) {
            if (isUnauthorized(queryError)) handleError(queryError);
            else setUnverifiedActionIds((previous) => new Set(previous).add(actionId));
          }
        }
      } finally {
        setBusyActionIds((previous) => without(previous, actionId));
      }
    },
    [busyActionIds, showAction, handleError],
  );

  /** “结果待核实”的卡片重新查询结果。只查询，不会再次确认或取消。 */
  const recheckAction = useCallback(
    async (actionId: number) => {
      if (checkingActionIds.has(actionId)) return;
      setCheckingActionIds((previous) => new Set(previous).add(actionId));
      try {
        showAction(await getAction(actionId));
      } catch (error) {
        if (isUnauthorized(error)) handleError(error);
        // 还是查不到：保持“结果待核实”，可以再试
      } finally {
        setCheckingActionIds((previous) => without(previous, actionId));
      }
    },
    [checkingActionIds, showAction, handleError],
  );

  const confirmProposal = useCallback(
    (actionId: number) => void respondToAction(actionId, "confirm"),
    [respondToAction],
  );
  const cancelProposal = useCallback(
    (actionId: number) => void respondToAction(actionId, "cancel"),
    [respondToAction],
  );

  const stop = useCallback(() => {
    stopRequested.current = true;
    replyRequest.current?.abort();
  }, []);

  return {
    conversations,
    activeId,
    messages,
    replying,
    busy: replying || creating,
    notice,
    busyActionIds,
    unverifiedActionIds,
    checkingActionIds,
    confirmProposal,
    cancelProposal,
    recheckAction: (actionId: number) => void recheckAction(actionId),
    selectConversation,
    startNewConversation,
    reloadConversation: () => void reloadConversation(),
    send,
    stop,
  };
}

function without(set: ReadonlySet<number>, id: number): ReadonlySet<number> {
  if (!set.has(id)) return set;
  const next = new Set(set);
  next.delete(id);
  return next;
}

function toDisplayMessage(message: ChatMessage): DisplayMessage {
  return {
    key: `message-${message.id}`,
    role: message.role,
    content: message.content,
    streaming: false,
    tools: [],
    action: message.action,
  };
}
