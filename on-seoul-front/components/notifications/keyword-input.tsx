"use client";

import { X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";

import { Input } from "@/components/ui/input";
import { KEYWORD_MAX_COUNT } from "@/types/notification";

interface KeywordInputProps {
  value: string[];
  onChange: (next: string[]) => void;
  /** 최대 칩 개수. 기본 3(명세 §5.4). */
  max?: number;
  disabled?: boolean;
}

/**
 * 태그 입력 컴포넌트.
 * Enter 또는 쉼표(,) 입력 시 input 값을 칩으로 추가한다.
 * - 빈 문자열 / 공백 / 중복은 무시
 * - 백스페이스로 빈 input에서 마지막 칩 삭제
 * - 최대치 도달 시 input 비활성화 + 안내
 *
 * 명세: docs/2026-05-28-frontend-personalized-notification.md §5.4
 */
export function KeywordInput({
  value,
  onChange,
  max = KEYWORD_MAX_COUNT,
  disabled,
}: KeywordInputProps) {
  const [draft, setDraft] = useState("");

  const atMax = value.length >= max;
  const inputDisabled = disabled || atMax;

  function commit(raw: string) {
    const trimmed = raw.trim();
    if (!trimmed) return;
    if (value.includes(trimmed)) {
      setDraft("");
      return;
    }
    if (value.length >= max) return;
    onChange([...value, trimmed]);
    setDraft("");
  }

  function removeAt(index: number) {
    onChange(value.filter((_, i) => i !== index));
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    // 한글 IME 조합 중 Enter는 변환 확정용이므로 commit하지 않는다.
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
      return;
    }
    if (e.key === "Backspace" && draft === "" && value.length > 0) {
      e.preventDefault();
      removeAt(value.length - 1);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5 rounded-md border border-border px-2 py-1.5">
        {value.map((kw, i) => (
          <span
            key={`${kw}-${i}`}
            className="inline-flex items-center gap-1 rounded-full bg-secondary px-2 py-0.5 text-xs"
          >
            <span>{kw}</span>
            <button
              type="button"
              onClick={() => removeAt(i)}
              disabled={disabled}
              aria-label={`키워드: ${kw} 삭제`}
              className="rounded-full p-0.5 text-muted-foreground hover:bg-muted disabled:opacity-50"
            >
              <X className="h-3 w-3" aria-hidden />
            </button>
          </span>
        ))}
        <Input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => commit(draft)}
          disabled={inputDisabled}
          aria-label="키워드 입력"
          placeholder={
            atMax ? "" : value.length === 0 ? "예: 수영" : "추가 키워드 입력"
          }
          className="h-7 flex-1 border-0 bg-transparent px-1 text-xs shadow-none focus-visible:ring-0 disabled:opacity-60"
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {atMax
          ? `최대 ${max}개까지 입력 가능합니다.`
          : `Enter 또는 쉼표(,)로 추가 · 최대 ${max}개`}
      </p>
    </div>
  );
}
