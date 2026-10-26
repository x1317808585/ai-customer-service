import { ActionCard } from "@/components/ActionCard";
import { formatDateTime } from "@/lib/admin/display";
import type { ChatMessage } from "@/lib/api";

/** 来源会话的聊天记录，只读：确认卡片也不带按钮，管理员不能替顾客确认。 */
export function TranscriptView({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) {
    return <p className="text-sm text-gray-400">这个会话里没有消息。</p>;
  }
  return (
    <ol aria-label="来源会话的聊天记录" className="space-y-3">
      {messages.map((message) => (
        <li
          key={message.id}
          className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}
        >
          <span className="mb-1 text-xs text-gray-400">
            {message.role === "user" ? "顾客" : "助手"} · {formatDateTime(message.created_at)}
          </span>
          {message.action ? (
            <ActionCard mode="readonly" action={message.action} />
          ) : (
            <div
              className={`max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm leading-relaxed ${
                message.role === "user" ? "bg-blue-600 text-white" : "bg-white text-gray-900 shadow-sm"
              }`}
            >
              {message.content}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
