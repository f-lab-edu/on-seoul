"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ChatInputProps {
  onSubmit: (question: string) => void;
  onCancel: () => void;
  streaming: boolean;
  disabled?: boolean;
}

/**
 * 채팅 입력. Enter 전송, Shift+Enter 줄바꿈, IME 조합 중에는 전송 차단.
 * streaming 중에는 입력 비활성화 + 취소 버튼 표시.
 */
export function ChatInput({ onSubmit, onCancel, streaming, disabled }: ChatInputProps) {
  const [value, setValue] = useState("");
  const [composing, setComposing] = useState(false);

  function trySubmit() {
    const q = value.trim();
    if (!q || streaming || disabled) return;
    onSubmit(q);
    setValue("");
  }

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    trySubmit();
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key !== "Enter") return;
    if (e.shiftKey) return;
    if (composing || e.nativeEvent.isComposing) return;
    e.preventDefault();
    trySubmit();
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-end gap-2 border-t border-border bg-background p-3"
    >
      <textarea
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={() => setComposing(false)}
        placeholder={streaming ? "응답을 받는 중입니다…" : "메시지를 입력하세요 (Shift+Enter 줄바꿈)"}
        rows={2}
        disabled={streaming || disabled}
        className={cn(
          "min-h-[44px] flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm",
          "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-60",
        )}
        aria-label="메시지 입력"
      />
      {streaming ? (
        <Button type="button" variant="outline" onClick={onCancel}>
          취소
        </Button>
      ) : (
        <Button type="submit" disabled={!value.trim() || disabled}>
          전송
        </Button>
      )}
    </form>
  );
}
