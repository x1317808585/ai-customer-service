"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isActionView, type ActionView } from "@/lib/actions";
import {
  ApiError,
  cancelAction,
  confirmAction,
  createConversation,
  describeError,
  getAction,
  isUnauthorized,
  listConversations,
  listMessages,
  sendMessage,
  type ChatMessage,
  type Conversation,
} from "@/lib/api";
import { applyReplyEvent, type ToolProgress } from "@/lib/replyDraft";
import { readSseEvents } from "@/lib/sse";

export type DisplayMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
  /** 生成过程中的工具调用进度，只在生成期间显示，不会保存。 */
  tools: ToolProgress[];
  /** 这条消息是确认卡片时，对应操作的当前状态 */
  action: ActionView | null;
};

type ReplyOutcome = "done" | "failed" | "stopped" | "interrupted";

const OUTCOME_NOTICES: Record<Exclude<ReplyOutcome, "done">, string> = {
  failed: "回复生成失败，请稍后重试。",
  stopped: "已停止生成。",
  interrupted: "连接中断，回复可能未完成。",
};

/** 聊天页面的全部状态和操作。组件只负责显示，以及把用户操作转交给这里。 */
export function useChat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [replying, setReplying] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyActionIds, setBusyActionIds] = useState<ReadonlySet<number>>(new Set());

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
    setNotice(describeError(error));
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
   * 读取历史并替换当前显示的消息，返回是否成功。
   * 新的读取会取消还没返回的旧读取，避免旧结果覆盖新会话。
   */
  const loadHistory = useCallback(async (conversationId: number): Promise<boolean> => {
    historyRequest.current?.abort();
    const controller = new AbortController();
    historyRequest.current = controller;
    try {
      const history = await listMessages(conversationId, controller.signal);
      setMessages(history.map(toDisplayMessage));
      return true;
    } catch (error) {
      if (!controller.signal.aborted) handleError(error);
      return false;
    }
  }, [handleError]);

  /** 切换到某个会话（或 null 表示还没有会话），清空当前显示的内容。 */
  const activate = useCallback((conversationId: number | null) => {
    historyRequest.current?.abort();
    activeIdRef.current = conversationId;
    setActiveId(conversationId);
    setMessages([]);
    setNotice(null);
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

  /** 发送消息。返回 false 表示服务器明确拒绝、什么都没保存，输入框应恢复原文。 */
  const send = useCallback(
    async (text: string): Promise<boolean> => {
      if (replying || creating) return false;
      setNotice(null);

      const conversationId = activeId ?? (await openNewConversation());
      if (conversationId === null) return false;

      const now = Date.now();
      const replyKey = `reply-${now}`;
      setMessages((previous) => [
        ...previous,
        { key: `question-${now}`, role: "user", content: text, streaming: false, tools: [], action: null },
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

      let outcome: ReplyOutcome = "interrupted";
      let failureDetail: string | null = null;
      try {
        const body = await sendMessage(conversationId, text, controller.signal);
        for await (const event of readSseEvents(body)) {
          if (event.event === "message.accepted") {
            void refreshConversations();
          } else if (event.event === "message.done") {
            outcome = "done";
            updateReply((message) => ({ ...message, streaming: false }));
          } else if (event.event === "error") {
            outcome = "failed";
            failureDetail = (JSON.parse(event.data) as { detail?: string }).detail ?? null;
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
        if (error instanceof ApiError) {
          setMessages((previous) => previous.slice(0, -2));
          handleError(error);
          return false;
        }
        outcome = stopRequested.current ? "stopped" : "interrupted";
      } finally {
        replyRequest.current = null;
        setReplying(false);
      }

      if (outcome !== "done") {
        setMessages((previous) => previous.filter((message) => message.key !== replyKey));
        const synced = await loadHistory(conversationId);
        const reason = outcome === "failed" && failureDetail ? `${failureDetail}。` : OUTCOME_NOTICES[outcome];
        setNotice(
          reason + (synced ? "已显示服务器上保存的记录。" : "暂时无法读取服务器上的记录，请稍后刷新页面。"),
        );
      }
      return true;
    },
    [replying, creating, activeId, openNewConversation, refreshConversations, loadHistory, handleError],
  );

  /** 用最新的操作状态替换页面上对应的卡片。 */
  const showAction = useCallback((action: ActionView) => {
    setMessages((previous) =>
      previous.map((message) => (message.action?.id === action.id ? { ...message, action } : message)),
    );
  }, []);

  /**
   * 点击卡片上的“确认提交”或“取消”。卡片完全按服务器返回的 status 显示。
   * 网络断开或服务器出错时，操作可能已经执行了，所以重新查询它的当前状态，而不是直接当作失败。
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
            setNotice("刚才的请求没有收到明确结果，已重新查询并显示操作的当前状态。");
          } catch {
            setNotice("暂时无法确认操作结果，请稍后刷新页面查看。");
          }
        }
      } finally {
        setBusyActionIds((previous) => {
          const next = new Set(previous);
          next.delete(actionId);
          return next;
        });
      }
    },
    [busyActionIds, showAction, handleError],
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
    confirmProposal,
    cancelProposal,
    selectConversation,
    startNewConversation,
    send,
    stop,
  };
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
