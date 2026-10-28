"use client";

import { useRef, useState } from "react";
import { CircleAlert, Info, TriangleAlert, type LucideIcon } from "lucide-react";

import { ConversationSidebar } from "@/components/customer/ConversationSidebar";
import { MessageComposer } from "@/components/customer/MessageComposer";
import { MessageList } from "@/components/customer/MessageList";
import { Button } from "@/components/ui/button";
import { useChat } from "@/hooks/customer/useChat";
import type { User } from "@/lib/api";
import type { Notice, NoticeLevel } from "@/lib/customer/replyOutcome";

type Props = {
  user: User;
  onLogOut: () => void;
};

export function ChatScreen({ user, onLogOut }: Props) {
  const chat = useChat();
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const title = chat.conversations.find((conversation) => conversation.id === chat.activeId)?.title ?? "新会话";

  function pickQuestion(text: string) {
    setDraft(text);
    inputRef.current?.focus();
  }

  return (
    <div className="flex h-screen">
      <ConversationSidebar
        conversations={chat.conversations}
        activeId={chat.activeId}
        disabled={chat.busy}
        username={user.username}
        showAdminEntry={user.role === "admin"}
        onSelect={chat.selectConversation}
        onCreate={chat.startNewConversation}
        onLogOut={onLogOut}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center border-b bg-card px-6">
          <h1 title={title} className="truncate text-base font-medium">
            {title}
          </h1>
        </header>
        {chat.notice && (
          <NoticeBar notice={chat.notice} reloadDisabled={chat.replying} onReload={chat.reloadConversation} />
        )}
        <MessageList
          messages={chat.messages}
          busyActionIds={chat.busyActionIds}
          unverifiedActionIds={chat.unverifiedActionIds}
          checkingActionIds={chat.checkingActionIds}
          onConfirmAction={chat.confirmProposal}
          onCancelAction={chat.cancelProposal}
          onRecheckAction={chat.recheckAction}
          onPickQuestion={pickQuestion}
        />
        <MessageComposer
          draft={draft}
          onDraftChange={setDraft}
          inputRef={inputRef}
          busy={chat.busy}
          replying={chat.replying}
          onSend={chat.send}
          onStop={chat.stop}
        />
      </main>
    </div>
  );
}

/** 三级提示：文字、图标加颜色。 */
const NOTICE_STYLES: Record<NoticeLevel, { icon: LucideIcon; className: string }> = {
  info: { icon: Info, className: "bg-status-neutral-bg text-status-neutral" },
  warning: { icon: TriangleAlert, className: "bg-status-pending-bg text-status-pending" },
  error: { icon: CircleAlert, className: "bg-status-danger-bg text-status-danger" },
};

function NoticeBar({
  notice,
  reloadDisabled,
  onReload,
}: {
  notice: Notice;
  reloadDisabled: boolean;
  onReload: () => void;
}) {
  const style = NOTICE_STYLES[notice.level];
  return (
    <div role="status" className={`flex items-center gap-2 px-6 py-2 text-sm ${style.className}`}>
      <style.icon aria-hidden className="size-4 shrink-0" />
      <p className="flex-1">{notice.text}</p>
      {notice.canReload && (
        <Button variant="outline" disabled={reloadDisabled} onClick={onReload} className="h-8 shrink-0 bg-card">
          重新加载会话
        </Button>
      )}
    </div>
  );
}
