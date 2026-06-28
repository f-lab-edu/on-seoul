"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";

import { ChatInput } from "@/components/chat/chat-input";
import { MessageList, type DisplayMessage } from "@/components/chat/message-list";
import { WelcomeMessage } from "@/components/chat/welcome-message";
import { Button } from "@/components/ui/button";
import { useChatStream } from "@/hooks/useChatStream";
import { CHAT_ROOMS_KEY } from "@/hooks/useChatRooms";
import { chatHistoryKey } from "@/hooks/useChatHistory";
import type { ChatHistoryResponse, RoomListResponse } from "@/types/chat-history";

interface ChatConversationProps {
  /** 기존 방이면 roomId. 새 대화(/chat)면 undefined. */
  roomId?: number;
  /** 기존 방 이력 시드. */
  initialMessages?: DisplayMessage[];
  /** 새 대화에서 비어 있을 때 소개/예시 질문 버블 노출. */
  welcome?: boolean;
}

/**
 * 대화 코어 — 메시지 누적 + 스트리밍 + 입력. `/chat`(새 대화)과 `/chat/[roomId]`(기존 방)가 공유한다.
 *
 * roomId 스레딩: 첫 질의로 새 방 생성 시 `init`(created:true)에서 받은 room_id를
 * 내부 ref에 보관하고, 라우터 네비게이션 대신 `history.replaceState`로 URL만 `/chat/{id}`로
 * 교체한다(컴포넌트 언마운트 없이 스트림 유지 — chat-sse-event-catalog 동선).
 */
export function ChatConversation({ roomId, initialMessages, welcome }: ChatConversationProps) {
  const currentRoomIdRef = useRef<number | undefined>(roomId);
  const qc = useQueryClient();
  // 새 대화에서 title 이벤트로 받은 생성 제목(라이브 헤더 표시용). 기존 방은 RoomDetail이 헤더를 가진다.
  const [liveTitle, setLiveTitle] = useState<string | null>(null);

  const { state, send, cancel, retry } = useChatStream({
    onInit: ({ roomId: rid, created }) => {
      currentRoomIdRef.current = rid;
      // 새 대화(URL에 roomId 없음)에서 방이 막 생성된 경우에만 URL 교체.
      if (created && roomId === undefined && typeof window !== "undefined") {
        window.history.replaceState(window.history.state, "", `/chat/${rid}`);
      }
    },
    onTitle: ({ roomId: rid, title }) => {
      setLiveTitle(title);
      // 목록 캐시: 해당 방이 이미 캐시에 있으면 제목만 갱신(없으면 no-op — 다음 조회 시 서버값).
      qc.setQueryData<InfiniteData<RoomListResponse>>(CHAT_ROOMS_KEY, (prev) =>
        prev
          ? {
              ...prev,
              pages: prev.pages.map((p) => ({
                ...p,
                rooms: p.rooms.map((r) => (r.roomId === rid ? { ...r, title } : r)),
              })),
            }
          : prev,
      );
      // 이력 캐시: 해당 방 캐시가 있으면 제목 갱신.
      qc.setQueryData<ChatHistoryResponse>(chatHistoryKey(rid), (prev) =>
        prev ? { ...prev, title } : prev,
      );
    },
  });

  const [messages, setMessages] = useState<DisplayMessage[]>(initialMessages ?? []);
  // done 이벤트를 한 번만 messages에 반영하기 위한 가드.
  const lastCommittedDoneId = useRef<number | null>(null);

  const doneMessageId = state.phase === "done" ? state.messageId : null;
  const doneContent = state.phase === "done" ? state.content : null;
  const doneServiceCards = state.phase === "done" ? state.serviceCards : null;
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
        serviceCards: doneServiceCards ?? [],
      },
    ]);
  }, [doneMessageId, doneContent, doneServiceCards]);

  function handleSubmit(question: string) {
    setMessages((prev) => [
      ...prev,
      { id: `user-${Date.now()}`, role: "USER", content: question },
    ]);
    void send({ question, roomId: currentRoomIdRef.current });
  }

  const streaming = state.phase === "streaming";
  const errored = state.phase === "error";
  const showWelcome = welcome === true && messages.length === 0 && state.phase === "idle";

  return (
    <>
      {/* 새 대화 한정 라이브 제목 헤더. 기존 방(roomId 지정)은 RoomDetail이 자체 헤더를 가진다. */}
      {roomId === undefined && liveTitle && (
        <div className="flex items-center border-b border-border px-4 py-2">
          <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{liveTitle}</h2>
        </div>
      )}

      <section className="flex-1 overflow-y-auto px-4 py-4">
        {showWelcome && <WelcomeMessage onQuestion={handleSubmit} />}
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

      <ChatInput onSubmit={handleSubmit} onCancel={cancel} streaming={streaming} />
    </>
  );
}
