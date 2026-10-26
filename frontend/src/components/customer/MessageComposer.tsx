import { useState, type FormEvent, type KeyboardEvent } from "react";

/** 与后端 MAX_MESSAGE_LENGTH 保持一致 */
const MAX_MESSAGE_LENGTH = 4000;

type Props = {
  busy: boolean;
  replying: boolean;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
};

export function MessageComposer({ busy, replying, onSend, onStop }: Props) {
  const [draft, setDraft] = useState("");
  const canSend = !busy && draft.trim() !== "";

  async function submit() {
    if (!canSend) return;
    const text = draft.trim();
    setDraft("");
    const accepted = await onSend(text);
    if (!accepted) setDraft(text);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // 中文输入法选词时按的回车不算发送；Shift+Enter 用来换行
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex items-end gap-2 border-t border-gray-200 p-4">
      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows={2}
        maxLength={MAX_MESSAGE_LENGTH}
        className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
      />
      {replying ? (
        <button
          type="button"
          onClick={onStop}
          className="rounded-lg bg-gray-700 px-4 py-2 text-sm text-white hover:bg-gray-800"
        >
          停止
        </button>
      ) : (
        <button
          type="submit"
          disabled={!canSend}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          发送
        </button>
      )}
    </form>
  );
}
