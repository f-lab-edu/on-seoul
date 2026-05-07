"use client";

import { useEffect, useRef } from "react";

import { AgentTrace } from "@/components/chat/agent-trace";
import { MessageBubble } from "@/components/chat/message-bubble";
import type { ChatStreamState } from "@/hooks/useChatStream";
import type { MessageRole } from "@/types/chat";

export interface DisplayMessage {
  id: string;
  role: MessageRole;
  content: string;
}

interface MessageListProps {
  messages: DisplayMessage[];
  streamState: ChatStreamState;
}

/**
 * 메시지 목록 + 진행 중인 스트림 표시.
 * 스트리밍 중인 ASSISTANT 응답은 messages 배열이 아닌 streamState로 분리해서 렌더한다
 * (done 시점에 부모에서 messages에 push).
 */
export function MessageList({ messages, streamState }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement | null>(null);

  // 새 메시지 추가/스트림 phase 전환 시 하단으로 스크롤.
  // streamState 객체 전체를 의존성으로 두면 매 토큰마다 재실행되어 불필요. phase + messages 길이만 추적.
  // 토큰 누적 중 부드러운 애니메이션은 오히려 뚝뚝 끊겨 보이므로 "auto"로 즉시 스크롤.
  const streamPhase = streamState.phase;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [messages.length, streamPhase]);

  const streamingContent =
    streamState.phase === "streaming" ? streamState.content : "";
  const showStreamBubble =
    streamState.phase === "streaming" && streamingContent.length > 0;
  const trace =
    streamState.phase === "streaming" ? streamState.trace : [];

  return (
    <div className="flex flex-col gap-3 pb-4">
      {messages.map((m) => (
        <MessageBubble key={m.id} role={m.role} content={m.content} />
      ))}

      {streamState.phase === "streaming" && trace.length > 0 && (
        <AgentTrace trace={trace} />
      )}

      {showStreamBubble && (
        <MessageBubble role="ASSISTANT" content={streamingContent} streaming />
      )}

      <div ref={bottomRef} />
    </div>
  );
}
