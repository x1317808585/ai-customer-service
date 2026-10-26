"use client";

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

  return (
    <main className="flex h-screen">
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
      <section className="flex min-w-0 flex-1 flex-col">
        {chat.notice && (
          <p role="status" className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-sm text-amber-800">
            {chat.notice}
          </p>
        )}
        <MessageList
          messages={chat.messages}
          busyActionIds={chat.busyActionIds}
          onConfirmAction={chat.confirmProposal}
          onCancelAction={chat.cancelProposal}
        />
        <MessageComposer
          busy={chat.busy}
          replying={chat.replying}
          onSend={chat.send}
          onStop={chat.stop}
        />
      </section>
    </main>
  );
}
