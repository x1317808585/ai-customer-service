import { useState } from "react";
import { ArrowDown, User } from "lucide-react";

import { ActionCard } from "@/components/ActionCard";
import { CitationList } from "@/components/CitationList";
import { HeyiMark } from "@/components/HeyiMark";
import { Button } from "@/components/ui/button";
import type { DisplayMessage } from "@/hooks/customer/useChat";
import { useStickToBottom } from "@/hooks/customer/useStickToBottom";
import { splitCitations } from "@/lib/citations";
import { formatMessageTime } from "@/lib/customer/display";
import { describeToolProgress } from "@/lib/customer/replyDraft";

/** 空会话里的快捷问题。点了只填进输入框，由用户补充后自己发送。 */
const QUICK_QUESTIONS = ["查一下我最近的订单", "我想申请退货", "查看我的工单进度"];

type Props = {
  messages: DisplayMessage[];
  busyActionIds: ReadonlySet<number>;
  unverifiedActionIds: ReadonlySet<number>;
  checkingActionIds: ReadonlySet<number>;
  onConfirmAction: (actionId: number) => void;
  onCancelAction: (actionId: number) => void;
  onRecheckAction: (actionId: number) => void;
  onPickQuestion: (text: string) => void;
};

export function MessageList({
  messages,
  busyActionIds,
  unverifiedActionIds,
  checkingActionIds,
  onConfirmAction,
  onCancelAction,
  onRecheckAction,
  onPickQuestion,
}: Props) {
  const { scrollRef, onScroll, hasNewMessages, scrollToBottom } = useStickToBottom(messages);
  // 判断消息是不是“今天”的，以打开页面的时刻为准
  const [openedAt] = useState(() => new Date());

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6">
        <p className="text-2xl font-semibold tracking-tight">你的问题，我们一起解决。</p>
        <div className="flex flex-wrap justify-center gap-2">
          {QUICK_QUESTIONS.map((question) => (
            <button
              key={question}
              type="button"
              onClick={() => onPickQuestion(question)}
              className="rounded-full border bg-card px-4 py-2 text-sm text-muted-foreground transition-colors duration-150 hover:border-primary hover:text-primary focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
            >
              {question}
            </button>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <ol aria-label="消息记录" className="mx-auto w-full max-w-[760px] space-y-5 px-6 py-6">
          {messages.map((message, index) =>
            message.role === "user" ? (
              <li key={message.key} className="flex items-start justify-end gap-3">
                <div className="flex max-w-[75%] min-w-0 flex-col items-end">
                  <MessageBubble message={message} />
                  <span className="mt-1 text-xs text-muted-foreground">
                    {formatMessageTime(message.createdAt, openedAt)}
                    {isUnanswered(messages, index) && " · 未收到回复"}
                  </span>
                </div>
                <UserAvatar />
              </li>
            ) : (
              <li key={message.key} className="flex gap-3">
                <AssistantAvatar visible={messages[index - 1]?.role !== "assistant"} />
                {message.action ? (
                  <ActionCard
                    mode="interactive"
                    action={message.action}
                    busy={busyActionIds.has(message.action.id)}
                    unverified={unverifiedActionIds.has(message.action.id)}
                    checking={checkingActionIds.has(message.action.id)}
                    onConfirm={onConfirmAction}
                    onCancel={onCancelAction}
                    onRecheck={onRecheckAction}
                  />
                ) : (
                  <MessageBubble message={message} />
                )}
              </li>
            ),
          )}
        </ol>
      </div>
      {hasNewMessages && (
        <Button
          variant="outline"
          onClick={scrollToBottom}
          className="absolute bottom-3 left-1/2 h-9 -translate-x-1/2 gap-1.5 rounded-full px-4 shadow-sm"
        >
          <ArrowDown aria-hidden />
          有新消息
        </Button>
      )}
    </div>
  );
}

/** 连续的助手消息只在第一条显示头像，后面的留出同样的宽度保持对齐。 */
function AssistantAvatar({ visible }: { visible: boolean }) {
  if (!visible) return <span aria-hidden className="w-9 shrink-0" />;
  return (
    <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-full border bg-card">
      <HeyiMark className="size-5 text-primary" />
    </span>
  );
}

function UserAvatar() {
  return (
    <span aria-hidden className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent text-primary">
      <User className="size-5" />
    </span>
  );
}

function MessageBubble({ message }: { message: DisplayMessage }) {
  const isUser = message.role === "user";
  return (
    <div
      className={`min-w-0 rounded-2xl px-4 py-2.5 text-base leading-[1.7] whitespace-pre-wrap [overflow-wrap:anywhere] ${
        isUser ? "bg-bubble-user" : "max-w-full bg-bubble-assistant"
      }`}
    >
      {message.streaming && message.tools.length > 0 && (
        <ul className="mb-1 space-y-0.5 text-[13px] text-muted-foreground">
          {message.tools.map((tool) => (
            <li key={tool.callId}>{describeToolProgress(tool)}</li>
          ))}
        </ul>
      )}
      {splitCitations(message.content, message.citations).map((part, index) =>
        part.kind === "text" ? (
          part.text
        ) : (
          <sup key={index} className="mx-0.5 text-xs font-medium text-primary">
            [{part.number}]
          </sup>
        ),
      )}
      {message.streaming && <span className="ml-0.5 animate-pulse motion-reduce:animate-none">▍</span>}
      {message.unfinished && <p className="mt-1 text-xs text-muted-foreground">回复未完成，未保存</p>}
      {message.citations.length > 0 && <CitationList citations={message.citations} />}
    </div>
  );
}

/** 用户消息后面紧跟的不是助手回复（下一条还是用户消息，或者它就是最后一条），说明这一轮没有回复。 */
function isUnanswered(messages: DisplayMessage[], index: number): boolean {
  const next = messages[index + 1];
  return messages[index].role === "user" && (next === undefined || next.role === "user");
}
