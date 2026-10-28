"use client";

import { useRef, useState } from "react";

import { ConversationSidebar } from "@/components/customer/ConversationSidebar";
import { MessageComposer } from "@/components/customer/MessageComposer";
import { MessageList } from "@/components/customer/MessageList";
import { useChat } from "@/hooks/customer/useChat";
import type { User } from "@/lib/api";

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
          <p role="status" className="bg-status-pending-bg px-6 py-2 text-sm text-status-pending">
            {chat.notice}
          </p>
        )}
        <MessageList
          messages={chat.messages}
          busyActionIds={chat.busyActionIds}
          onConfirmAction={chat.confirmProposal}
          onCancelAction={chat.cancelProposal}
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
