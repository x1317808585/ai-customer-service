import { ActionCard } from "@/components/ActionCard";
import { CitationList } from "@/components/CitationList";
import { formatDateTime } from "@/lib/admin/display";
import type { ChatMessage } from "@/lib/api";
import { splitCitations } from "@/lib/citations";

/**
 * 来源会话的聊天记录，只读：确认卡片也不带按钮，管理员不能替顾客确认。
 *
 * 助手回复里的引用和顾客看到的一样，显示成编号和可以展开的参考资料。
 * 内容是回复保存时的快照，不会拿当前的知识库重新查，知识文档后来更新了也不影响这里。
 */
export function TranscriptView({ messages }: { messages: ChatMessage[] }) {
  if (messages.length === 0) {
    return <p className="text-sm text-muted-foreground">这个会话里没有消息。</p>;
  }
  return (
    <ol aria-label="来源会话的聊天记录" className="space-y-3">
      {messages.map((message) => (
        <li
          key={message.id}
          className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}
        >
          <span className="mb-1 text-xs text-muted-foreground">
            {message.role === "user" ? "顾客" : "助手"} · {formatDateTime(message.created_at)}
          </span>
          {message.action ? (
            <ActionCard mode="readonly" action={message.action} />
          ) : (
            <div
              className={`max-w-[75%] rounded-2xl px-4 py-2 text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${
                message.role === "user" ? "bg-bubble-user" : "border bg-card"
              }`}
            >
              <MessageText message={message} />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

/** 消息正文：已核对的引用标记换成编号，有引用时在下面列出参考资料。 */
function MessageText({ message }: { message: ChatMessage }) {
  const citations = message.citations ?? [];
  return (
    <>
      {splitCitations(message.content, citations).map((part, index) =>
        part.kind === "text" ? (
          part.text
        ) : (
          <sup key={index} className="mx-0.5 text-xs font-medium text-primary">
            [{part.number}]
          </sup>
        ),
      )}
      {citations.length > 0 && <CitationList citations={citations} />}
    </>
  );
}
