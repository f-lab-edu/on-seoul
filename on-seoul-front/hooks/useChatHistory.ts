"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ChatHistoryResponse } from "@/types/chat-history";

export const chatHistoryKey = (roomId: number) =>
  ["chat", "rooms", roomId, "messages"] as const;

/** 답변 생성 중 재진입 폴링 간격(ms). */
const PENDING_POLL_MS = 2500;

/**
 * 단일 대화방의 전체 메시지 이력 조회(seq 오름차순, 페이지네이션 없음).
 * 미존재/타인 소유/삭제된 방은 404 CHAT_ROOM_NOT_FOUND(가이드 §4.3).
 *
 * 마지막 메시지가 USER(답변 미생성)면 백엔드가 답변을 생성·저장 중인 상태이므로
 * ASSISTANT가 채워질 때까지 자동 폴링한다(백엔드 disconnect 내성과 짝). 답이 생기면 멈춘다.
 */
export function useChatHistory(roomId: number): UseQueryResult<ChatHistoryResponse, Error> {
  return useQuery<ChatHistoryResponse, Error>({
    queryKey: chatHistoryKey(roomId),
    queryFn: () =>
      apiClient.get<ChatHistoryResponse>(`/api/chat/rooms/${roomId}/messages`),
    // 삭제 직후 재조회 시 404가 정상이므로 자동 재시도하지 않는다.
    retry: false,
    staleTime: 30_000,
    refetchInterval: (query) => {
      const msgs = query.state.data?.messages;
      if (!msgs || msgs.length === 0) return false;
      return msgs[msgs.length - 1]?.role === "USER" ? PENDING_POLL_MS : false;
    },
  });
}
