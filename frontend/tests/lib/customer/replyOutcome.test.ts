import { describe, expect, it } from "vitest";

import { settleReply, type ReplyEnding } from "@/lib/customer/replyOutcome";

describe("settleReply", () => {
  it.each<[ReplyEnding, boolean, boolean, string, string, boolean]>([
    // 结束方式, 会话记录读到了吗, 恢复输入框, 提示级别, 提示文字, 显示重新加载
    [{ kind: "rejected", detail: "上一条回复还在生成中" }, true, true, "error", "上一条回复还在生成中", false],
    [{ kind: "unsent" }, true, true, "warning", "消息可能已发送，请先核对会话记录，再决定是否重发。", true],
    [
      { kind: "unsent" },
      false,
      true,
      "warning",
      "暂时无法核对会话记录，消息可能已发送。请稍后重新加载核对，再决定是否重发。",
      true,
    ],
    [{ kind: "failed", detail: "回复生成失败，请稍后重试" }, true, false, "error", "回复生成失败，请稍后重试。", false],
    [{ kind: "failed", detail: null }, false, false, "error", "回复生成失败，请稍后重试。暂时无法读取会话记录。", true],
    [{ kind: "interrupted" }, true, false, "warning", "连接中断，回复可能未完成。", true],
    [{ kind: "interrupted" }, false, false, "warning", "连接中断，回复可能未完成。暂时无法读取会话记录。", true],
    [{ kind: "stopped" }, true, false, "info", "已停止生成。", false],
    [{ kind: "stopped" }, false, false, "info", "已停止生成。暂时无法读取会话记录。", true],
  ])("%j，记录读取成功=%s", (ending, loaded, restoreInput, level, text, canReload) => {
    expect(settleReply(ending, loaded)).toEqual({ restoreInput, notice: { level, text, canReload } });
  });
});
