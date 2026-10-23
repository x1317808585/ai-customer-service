"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  createConversation,
  listConversations,
  listMessages,
  sendMessage,
  type ChatMessage,
  type Conversation,
} from "@/lib/api";
import { readSseEvents } from "@/lib/sse";

export type DisplayMessage = {
  key: string;
  role: "user" | "assistant";
  content: string;
  streaming: boolean;
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

  const activeIdRef = useRef<number | null>(null);
  const latestListRequest = useRef(0);
  const historyRequest = useRef<AbortController | null>(null);
  const replyRequest = useRef<AbortController | null>(null);
  const stopRequested = useRef(false);

  /** 重新读取会话列表。只采用最新一次请求的结果，旧请求晚到时直接丢弃。 */
  const refreshConversations = useCallback(async (): Promise<Conversation[] | null> => {
    const requestId = ++latestListRequest.current;
    try {
      const list = await listConversations();
      if (requestId !== latestListRequest.current) return null;
      setConversations(list);
      return list;
    } catch (error) {
      setNotice(describeError(error));
      return null;
    }
  }, []);

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
      if (!controller.signal.aborted) setNotice(describeError(error));
      return false;
    }
  }, []);

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
      setNotice(describeError(error));
      return null;
    } finally {
      setCreating(false);
    }
  }, [activate, refreshConversations]);

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
        { key: `question-${now}`, role: "user", content: text, streaming: false },
        { key: replyKey, role: "assistant", content: "", streaming: true },
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
      try {
        const body = await sendMessage(conversationId, text, controller.signal);
        for await (const event of readSseEvents(body)) {
          if (event.event === "message.accepted") {
            void refreshConversations();
          } else if (event.event === "message.delta") {
            const { text: piece } = JSON.parse(event.data) as { text: string };
            updateReply((message) => ({ ...message, content: message.content + piece }));
          } else if (event.event === "message.done") {
            outcome = "done";
            updateReply((message) => ({ ...message, streaming: false }));
          } else if (event.event === "error") {
            outcome = "failed";
          }
        }
      } catch (error) {
        if (error instanceof ApiError) {
          setMessages((previous) => previous.slice(0, -2));
          setNotice(error.message);
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
        setNotice(
          OUTCOME_NOTICES[outcome] +
            (synced ? "已显示服务器上保存的记录。" : "暂时无法读取服务器上的记录，请稍后刷新页面。"),
        );
      }
      return true;
    },
    [replying, creating, activeId, openNewConversation, refreshConversations, loadHistory],
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
  };
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return "无法连接服务器，请确认后端已启动。";
  return error instanceof Error ? error.message : "发生未知错误，请刷新页面重试。";
}
