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

  // iOS 홈인디케이터/안드로이드 제스처바에 입력창이 가리지 않도록 safe-area-inset-bottom을 패딩에 더한다.
  // 모바일 키보드가 올라오면 visualViewport 기준 dvh가 줄어들어 form은 자연스럽게 키보드 위로 밀린다.
  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-end gap-2 border-t border-border bg-background px-3 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]"
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
        // iOS Safari가 16px 미만 input에 자동 줌인하는 것을 막기 위해 모바일에서 text-base 강제.
        className={cn(
          "min-h-[44px] flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-base sm:text-sm",
          "placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:cursor-not-allowed disabled:opacity-60",
        )}
        aria-label="메시지 입력"
      />
      {streaming ? (
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          className="h-11 min-w-11 px-4"
        >
          취소
        </Button>
      ) : (
        <Button
          type="submit"
          disabled={!value.trim() || disabled}
          className="h-11 min-w-11 px-4"
        >
          전송
        </Button>
      )}
    </form>
  );
}
