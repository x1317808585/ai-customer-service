import type { Conversation } from "@/lib/api";

type Props = {
  conversations: Conversation[];
  activeId: number | null;
  disabled: boolean;
  onSelect: (conversationId: number) => void;
  onCreate: () => void;
};

export function ConversationSidebar({ conversations, activeId, disabled, onSelect, onCreate }: Props) {
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
    </aside>
  );
}
