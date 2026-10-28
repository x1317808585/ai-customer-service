import { useLayoutEffect, useRef, useState, type UIEvent } from "react";

import type { DisplayMessage } from "@/hooks/customer/useChat";

/** 离底部不到这么多像素，就算停在底部。 */
const BOTTOM_THRESHOLD = 40;

/**
 * 消息区的滚动：停在底部时跟着新内容往下走；用户往上翻看时不动，改为提示“有新消息”。
 * 切换会话、用户自己发了消息时，总是回到底部。
 */
export function useStickToBottom(messages: DisplayMessage[]) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  /** 用户离开底部那一刻的内容；之后内容变了就说明有新消息。null 表示停在底部。 */
  const [leftAt, setLeftAt] = useState<string | null>(null);

  const content = contentSignature(messages);
  // 会话的第一条消息或用户最后发的消息变了：换了会话，或者用户刚发了消息
  const anchor = `${messages[0]?.key}|${messages.findLast((message) => message.role === "user")?.key}`;
  const lastAnchor = useRef(anchor);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    if (anchor !== lastAnchor.current) {
      lastAnchor.current = anchor;
      following.current = true;
    }
    if (following.current) scroller.scrollTop = scroller.scrollHeight;
  }, [content, anchor]);

  function onScroll(event: UIEvent<HTMLDivElement>) {
    const scroller = event.currentTarget;
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < BOTTOM_THRESHOLD;
    following.current = atBottom;
    setLeftAt((previous) => (atBottom ? null : (previous ?? content)));
  }

  function scrollToBottom() {
    const scroller = scrollRef.current;
    if (!scroller) return;
    following.current = true;
    scroller.scrollTop = scroller.scrollHeight;
  }

  return { scrollRef, onScroll, hasNewMessages: leftAt !== null && leftAt !== content, scrollToBottom };
}

/** 消息区的内容摘要：条数、最后一条的长度和工具进度。变了就说明有新内容。 */
function contentSignature(messages: DisplayMessage[]): string {
  const last = messages.at(-1);
  return `${messages.length}:${last?.content.length ?? 0}:${last?.tools.length ?? 0}`;
}
