import ReactMarkdown from "react-markdown";

import { cn } from "@/lib/utils";
import type { MessageRole } from "@/types/chat";

interface MessageBubbleProps {
  role: MessageRole;
  content: string;
  /** 스트리밍 중이면 ASSISTANT 버블에 커서 표시. */
  streaming?: boolean;
}

// 마크다운 자식 요소 여백/리스트 스타일. typography 플러그인 없이 최소만.
// react-markdown은 기본적으로 raw HTML을 렌더하지 않으므로 `<청년소통극장>` 같은
// 꺾쇠 텍스트는 그대로 노출된다(XSS 안전).
const MARKDOWN_STYLES =
  "[&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 " +
  "[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 " +
  "[&_li]:my-0.5 [&_strong]:font-semibold [&_a]:underline [&_a]:underline-offset-2";

/**
 * 메시지 버블. USER는 우측, ASSISTANT는 좌측.
 * USER는 평문(`whitespace-pre-wrap`), ASSISTANT는 마크다운 렌더.
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
          "max-w-[80%] break-words rounded-2xl px-4 py-2 text-sm leading-relaxed",
          isUser
            ? "bg-primary text-primary-foreground rounded-br-sm whitespace-pre-wrap"
            : "bg-muted text-foreground rounded-bl-sm",
          !isUser && MARKDOWN_STYLES,
        )}
        data-role={role}
      >
        {isUser ? content : <ReactMarkdown>{content}</ReactMarkdown>}
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
