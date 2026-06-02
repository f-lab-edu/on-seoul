"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { ChatHistoryResponse } from "@/types/chat-history";

export const chatHistoryKey = (roomId: number) =>
  ["chat", "rooms", roomId, "messages"] as const;

/**
 * 단일 대화방의 전체 메시지 이력 조회(seq 오름차순, 페이지네이션 없음).
 * 미존재/타인 소유/삭제된 방은 404 CHAT_ROOM_NOT_FOUND(가이드 §4.3).
 */
export function useChatHistory(roomId: number): UseQueryResult<ChatHistoryResponse, Error> {
  return useQuery<ChatHistoryResponse, Error>({
    queryKey: chatHistoryKey(roomId),
    queryFn: () =>
      apiClient.get<ChatHistoryResponse>(`/api/chat/rooms/${roomId}/messages`),
    // 삭제 직후 재조회 시 404가 정상이므로 자동 재시도하지 않는다.
    retry: false,
    staleTime: 30_000,
  });
}
