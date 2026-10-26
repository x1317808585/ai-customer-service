import type { Conversation } from "@/lib/customer/api";

type Props = {
  conversations: Conversation[];
  activeId: number | null;
  disabled: boolean;
  username: string;
  onSelect: (conversationId: number) => void;
  onCreate: () => void;
  onLogOut: () => void;
};

export function ConversationSidebar({
  conversations,
  activeId,
  disabled,
  username,
  onSelect,
  onCreate,
  onLogOut,
}: Props) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-gray-200 bg-gray-50">
      <button
        type="button"
        onClick={onCreate}
        disabled={disabled}
        className="m-3 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50"
      >
        ＋ 新建会话
      </button>
      <nav className="flex-1 overflow-y-auto px-2 pb-3">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            onClick={() => onSelect(conversation.id)}
            disabled={disabled}
            className={`mb-1 block w-full truncate rounded-lg px-3 py-2 text-left text-sm disabled:cursor-not-allowed ${
              conversation.id === activeId ? "bg-blue-100 text-blue-900" : "hover:bg-gray-200"
            }`}
          >
            {conversation.title}
          </button>
        ))}
      </nav>
      <div className="flex items-center justify-between border-t border-gray-200 px-4 py-3 text-sm">
        <span className="truncate text-gray-600">{username}</span>
        <button type="button" onClick={onLogOut} className="text-gray-500 hover:text-gray-900">
          退出
        </button>
      </div>
    </aside>
  );
}
