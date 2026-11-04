import { useLayoutEffect, type FormEvent, type KeyboardEvent, type RefObject } from "react";
import { ArrowUp, Square } from "lucide-react";

import { Button } from "@/components/ui/button";

/** 与后端 MAX_MESSAGE_LENGTH 保持一致 */
const MAX_MESSAGE_LENGTH = 4000;
/** 输入框最多长到这么高，再多的内容在框内滚动 */
const MAX_INPUT_HEIGHT = 200;

type Props = {
  /** 输入框里的内容。由上层保存，快捷问题可以直接填进来。 */
  draft: string;
  onDraftChange: (text: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  busy: boolean;
  replying: boolean;
  onSend: (text: string) => Promise<boolean>;
  onStop: () => void;
};

export function MessageComposer({ draft, onDraftChange, inputRef, busy, replying, onSend, onStop }: Props) {
  const canSend = !busy && draft.trim() !== "";

  // 输入框随内容变高，到上限后在框内滚动
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, MAX_INPUT_HEIGHT)}px`;
  }, [draft, inputRef]);

  async function submit() {
    if (!canSend) return;
    const text = draft.trim();
    onDraftChange("");
    const accepted = await onSend(text);
    if (!accepted) onDraftChange(text);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // 中文输入法选词时按的回车不算发送（Safari 选词时 isComposing 可能已经是 false，只能看 keyCode 229）；
    // Shift+Enter 用来换行
    const composing = event.nativeEvent.isComposing || event.keyCode === 229;
    if (event.key === "Enter" && !event.shiftKey && !composing) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <div className="shrink-0 px-6 pb-6">
      <form
        onSubmit={handleSubmit}
        className="mx-auto w-full max-w-[760px] rounded-xl border border-input bg-card px-3 pt-3 pb-2 shadow-sm transition-shadow duration-150 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/25"
      >
        <div className="flex items-start gap-2">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="描述你遇到的问题…"
            aria-label="输入消息"
            rows={2}
            maxLength={MAX_MESSAGE_LENGTH}
            className="min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-1 py-1 text-base leading-[1.7] outline-none placeholder:text-muted-foreground"
          />
          {replying ? (
            <Button type="button" variant="secondary" onClick={onStop} aria-label="停止" className="size-10">
              <Square aria-hidden className="fill-current" />
            </Button>
          ) : (
            <Button type="submit" disabled={!canSend} aria-label="发送" className="size-10 hover:bg-primary-hover">
              <ArrowUp aria-hidden className="size-5" />
            </Button>
          )}
        </div>
        <p className="mt-1 px-1 text-right text-xs text-muted-foreground">Enter 发送 · Shift + Enter 换行</p>
      </form>
    </div>
  );
}
