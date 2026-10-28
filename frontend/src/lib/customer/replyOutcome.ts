/**
 * 一次回复没有正常结束时，页面该怎么收尾：输入框要不要恢复原文、提示什么。
 *
 * 除了 rejected（服务器明确拒绝，什么都没保存），其余情况页面都会先重新读取会话记录，
 * 再把“读取是否成功”一起交给这里决定提示。读取失败时不能暗示消息没发出去。
 */

/** 回复是怎么结束的（正常结束不需要收尾，不在这里）。 */
export type ReplyEnding =
  /** 发送时服务器返回 4xx：明确拒绝，什么都没保存 */
  | { kind: "rejected"; detail: string }
  /** 还没收到 message.accepted 就断网或收到 5xx：不知道用户消息有没有保存 */
  | { kind: "unsent" }
  /** 收到 accepted 之后服务器发来 error 事件：用户消息已保存，回复没保存 */
  | { kind: "failed"; detail: string | null }
  /** 收到 accepted 之后连接断开：同上，而且服务器可能还在生成 */
  | { kind: "interrupted" }
  /** 用户点了停止 */
  | { kind: "stopped" };

export type NoticeLevel = "info" | "warning" | "error";

/** 页面顶部的提示。canReload 为 true 时提示旁边显示“重新加载会话”按钮。 */
export type Notice = { level: NoticeLevel; text: string; canReload: boolean };

export type Settlement = {
  /** 把用户刚才输入的内容放回输入框 */
  restoreInput: boolean;
  notice: Notice;
};

const UNREADABLE = "暂时无法读取会话记录。";

export function settleReply(ending: ReplyEnding, historyLoaded: boolean): Settlement {
  const canReload = !historyLoaded || ending.kind === "unsent" || ending.kind === "interrupted";
  const suffix = historyLoaded ? "" : UNREADABLE;
  switch (ending.kind) {
    case "rejected":
      return { restoreInput: true, notice: { level: "error", text: ending.detail, canReload: false } };
    case "unsent":
      return {
        restoreInput: true,
        notice: {
          level: "warning",
          text: historyLoaded
            ? "消息可能已发送，请先核对会话记录，再决定是否重发。"
            : "暂时无法核对会话记录，消息可能已发送。请稍后重新加载核对，再决定是否重发。",
          canReload,
        },
      };
    case "failed":
      return {
        restoreInput: false,
        notice: { level: "error", text: `${withPeriod(ending.detail ?? "回复生成失败，请稍后重试")}${suffix}`, canReload },
      };
    case "interrupted":
      return {
        restoreInput: false,
        notice: { level: "warning", text: `连接中断，回复可能未完成。${suffix}`, canReload },
      };
    case "stopped":
      return { restoreInput: false, notice: { level: "info", text: `已停止生成。${suffix}`, canReload } };
  }
}

function withPeriod(text: string): string {
  return /[。！？.!?]$/.test(text) ? text : `${text}。`;
}
