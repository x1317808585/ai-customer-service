import { useEffect, useRef } from "react";

import { ActionCard } from "@/components/ActionCard";
import type { DisplayMessage } from "@/hooks/customer/useChat";
import { describeToolProgress } from "@/lib/customer/replyDraft";

type Props = {
  messages: DisplayMessage[];
  busyActionIds: ReadonlySet<number>;
  onConfirmAction: (actionId: number) => void;
  onCancelAction: (actionId: number) => void;
};

export function MessageList({ messages, busyActionIds, onConfirmAction, onCancelAction }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-gray-400">
        有什么可以帮你？
      </div>
    );
  }

  return (
    <div className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
      {messages.map((message, index) => (
        <div
          key={message.key}
          className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}
        >
          {message.action ? (
            <ActionCard
              mode="interactive"
              action={message.action}
              busy={busyActionIds.has(message.action.id)}
              onConfirm={onConfirmAction}
              onCancel={onCancelAction}
            />
          ) : (
            <MessageBubble message={message} />
          )}
          {isUnanswered(messages, index) && (
            <span className="mt-1 text-xs text-gray-400">未收到回复</span>
          )}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function MessageBubble({ message }: { message: DisplayMessage }) {
  return (
    <div
      data-role={message.role}
      className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
        message.role === "user" ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"
      }`}
    >
      {message.streaming && message.tools.length > 0 && (
        <ul className="mb-1 space-y-0.5 text-xs text-gray-500">
          {message.tools.map((tool) => (
            <li key={tool.callId}>{describeToolProgress(tool)}</li>
          ))}
        </ul>
      )}
      {message.content}
      {message.streaming && <span className="ml-0.5 animate-pulse">▍</span>}
    </div>
  );
}

/** 用户消息后面紧跟的不是助手回复（下一条还是用户消息，或者它就是最后一条），说明这一轮没有回复。 */
function isUnanswered(messages: DisplayMessage[], index: number): boolean {
  const next = messages[index + 1];
  return messages[index].role === "user" && (next === undefined || next.role === "user");
}
