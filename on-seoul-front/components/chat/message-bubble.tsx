import { cn } from "@/lib/utils";
import type { MessageRole } from "@/types/chat";

interface MessageBubbleProps {
  role: MessageRole;
  content: string;
  /** 스트리밍 중이면 ASSISTANT 버블에 커서 표시. */
  streaming?: boolean;
}

/**
 * 메시지 버블. USER는 우측, ASSISTANT는 좌측.
 * MVP: 마크다운 렌더 대신 `whitespace-pre-wrap`으로 줄바꿈만 보존한다.
 * (post-MVP에 react-markdown 도입 예정 — 코드블록/링크 렌더링 강화)
 */
export function MessageBubble({ role, content, streaming = false }: MessageBubbleProps) {
  const isUser = role === "USER";
  return (
    <div
      className={cn(
        "flex w-full",
        isUser ? "justify-end" : "justify-start",
      )}
    >
      <div
        className={cn(
          "max-w-[80%] whitespace-pre-wrap break-words rounded-2xl px-4 py-2 text-sm leading-relaxed",
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm"
            : "bg-muted text-foreground rounded-bl-sm",
        )}
        data-role={role}
      >
        {content}
        {streaming && !isUser && (
          <span
            aria-hidden="true"
            className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 bg-current align-middle motion-safe:animate-pulse"
          />
        )}
      </div>
    </div>
  );
}
