"use client";

import { useEffect, useRef, useState } from "react";

import { ChatInput } from "@/components/chat/chat-input";
import { MessageList, type DisplayMessage } from "@/components/chat/message-list";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { useChatStream } from "@/hooks/useChatStream";

/**
 * 챗봇 메인 페이지 (새 대화 시작).
 *
 * 메시지 라이프사이클:
 * - USER 메시지: 전송 즉시 messages에 push.
 * - ASSISTANT 메시지: streaming 중에는 useChatStream 상태로만 표시.
 *   `done` 이벤트 수신 후 messages에 확정 push.
 * - 스트림 중 단절(error 또는 비정상 종료): 사용자에게 에러 + 재시도 버튼 노출 (절대규칙 B.4).
 */
export default function ChatPage() {
  const { user, loading, error: authError, logout } = useAuth();
  const { state, send, cancel, retry } = useChatStream();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  // done 이벤트를 한 번만 messages에 반영하기 위한 가드.
  const lastCommittedDoneId = useRef<number | null>(null);

  const doneMessageId = state.phase === "done" ? state.messageId : null;
  const doneContent = state.phase === "done" ? state.content : null;
  useEffect(() => {
    if (doneMessageId === null) return;
    if (lastCommittedDoneId.current === doneMessageId) return;
    lastCommittedDoneId.current = doneMessageId;
    setMessages((prev) => [
      ...prev,
      {
        id: `assistant-${doneMessageId}`,
        role: "ASSISTANT",
        content: doneContent ?? "",
      },
    ]);
  }, [doneMessageId, doneContent]);

  function handleSubmit(question: string) {
    setMessages((prev) => [
      ...prev,
      { id: `user-${Date.now()}`, role: "USER", content: question },
    ]);
    void send({ question });
  }

  const streaming = state.phase === "streaming";
  const errored = state.phase === "error";

  return (
    <main className="mx-auto flex h-dvh max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h1 className="text-base font-medium">서울시 AI 도우미</h1>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {loading ? (
            <span>불러오는 중…</span>
          ) : user ? (
            <>
              <span>{user.nickname}</span>
              <Button variant="outline" size="sm" onClick={() => void logout()}>
                로그아웃
              </Button>
            </>
          ) : null}
        </div>
      </header>

      {authError && (
        <p
          role="alert"
          className="border-b border-destructive/30 bg-destructive/5 px-4 py-2 text-xs text-destructive"
        >
          사용자 정보를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.
        </p>
      )}

      <section className="flex-1 overflow-y-auto px-4 py-4">
        <MessageList messages={messages} streamState={state} />

        {errored && (
          <div
            role="alert"
            className="mt-3 flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
          >
            <p>응답을 받지 못했습니다: {state.message}</p>
            <div>
              <Button variant="outline" size="sm" onClick={() => void retry()}>
                다시 시도
              </Button>
            </div>
          </div>
        )}
      </section>

      <ChatInput
        onSubmit={handleSubmit}
        onCancel={cancel}
        streaming={streaming}
        disabled={loading}
      />
    </main>
  );
}
