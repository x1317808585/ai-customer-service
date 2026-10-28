import { useState } from "react";
import Link from "next/link";
import { LogOut, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Conversation } from "@/lib/customer/api";
import { groupConversations } from "@/lib/customer/display";

type Props = {
  conversations: Conversation[];
  activeId: number | null;
  disabled: boolean;
  username: string;
  /** 管理员在聊天页测试助手时，显示回到管理工作台的入口 */
  showAdminEntry: boolean;
  onSelect: (conversationId: number) => void;
  onCreate: () => void;
  onLogOut: () => void;
};

export function ConversationSidebar({
  conversations,
  activeId,
  disabled,
  username,
  showAdminEntry,
  onSelect,
  onCreate,
  onLogOut,
}: Props) {
  // “今天”以打开页面的时刻为准，页面一直开着跨过午夜也不重新分组
  const [openedAt] = useState(() => new Date());
  const groups = groupConversations(conversations, openedAt);

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex h-16 shrink-0 items-center px-5 text-lg font-semibold tracking-tight">合意</div>
      <div className="px-3">
        <Button variant="outline" onClick={onCreate} disabled={disabled} className="h-10 w-full justify-start gap-2">
          <Plus aria-hidden />
          新建会话
        </Button>
      </div>
      <nav aria-label="历史会话" className="mt-4 flex-1 overflow-y-auto px-3 pb-3">
        {groups.map((group) => (
          <section key={group.label} className="mb-4">
            <h2 className="px-3 pb-1 text-xs text-muted-foreground">{group.label}</h2>
            {group.conversations.map((conversation) => {
              const active = conversation.id === activeId;
              return (
                <button
                  key={conversation.id}
                  type="button"
                  onClick={() => onSelect(conversation.id)}
                  disabled={disabled}
                  aria-current={active ? "true" : undefined}
                  title={conversation.title}
                  className={`mb-0.5 block h-10 w-full truncate rounded-lg px-3 text-left text-sm transition-colors duration-150 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-60 ${
                    active ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground" : "hover:bg-secondary"
                  }`}
                >
                  {conversation.title}
                </button>
              );
            })}
          </section>
        ))}
      </nav>
      <div className="flex items-center gap-2 border-t border-sidebar-border px-3 py-3 text-sm">
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-medium uppercase"
        >
          {username.slice(0, 1)}
        </span>
        <span className="min-w-0 flex-1 truncate">{username}</span>
        {showAdminEntry && (
          <Link href="/admin" className="shrink-0 text-primary hover:underline">
            管理工作台
          </Link>
        )}
        <Button variant="ghost" onClick={onLogOut} className="h-8 shrink-0 gap-1 text-muted-foreground">
          <LogOut aria-hidden />
          退出
        </Button>
      </div>
    </aside>
  );
}
