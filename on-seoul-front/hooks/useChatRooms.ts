"use client";

import {
  useInfiniteQuery,
  type InfiniteData,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { RoomListResponse } from "@/types/chat-history";

export const CHAT_ROOMS_KEY = ["chat", "rooms"] as const;
const PAGE_SIZE = 20;

/**
 * 대화방 목록(커서 페이지네이션) 조회.
 * - 서버 정렬(updatedAt DESC, roomId DESC)을 그대로 노출하고 클라이언트 재정렬 금지(가이드 §3.1).
 * - `nextCursor`는 불투명 토큰 — 파싱/가공 없이 받은 값을 그대로 다음 cursor로 전달(가이드 §4.2).
 *   `null`이면 마지막 페이지이므로 더 요청하지 않는다.
 */
export function useChatRooms(): UseInfiniteQueryResult<
  InfiniteData<RoomListResponse>,
  Error
> {
  return useInfiniteQuery<
    RoomListResponse,
    Error,
    InfiniteData<RoomListResponse>,
    typeof CHAT_ROOMS_KEY,
    string | null
  >({
    queryKey: CHAT_ROOMS_KEY,
    initialPageParam: null,
    queryFn: async ({ pageParam }) => {
      const params = new URLSearchParams({ size: String(PAGE_SIZE) });
      if (pageParam !== null) params.set("cursor", pageParam);
      return apiClient.get<RoomListResponse>(`/api/chat/rooms?${params.toString()}`);
    },
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    staleTime: 30_000,
  });
}
