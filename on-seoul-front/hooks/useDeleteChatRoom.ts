"use client";

import {
  useMutation,
  useQueryClient,
  type InfiniteData,
  type UseMutationResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import { CHAT_ROOMS_KEY } from "@/hooks/useChatRooms";
import type { RoomListResponse } from "@/types/chat-history";

interface DeleteContext {
  previous: InfiniteData<RoomListResponse> | undefined;
}

/**
 * 대화방 soft delete.
 * 낙관적으로 목록 캐시에서 해당 방을 제거하고, 실패 시 롤백한다(가이드 §3.3 / §8).
 * 미존재/타인 소유/이미 삭제됨은 모두 404지만, 목록에서 이미 사라졌으므로 사용자에겐
 * "삭제됨"과 사실상 동일한 결과 — 롤백 후 토스트로 안내한다.
 */
export function useDeleteChatRoom(): UseMutationResult<void, Error, number, DeleteContext> {
  const qc = useQueryClient();
  return useMutation<void, Error, number, DeleteContext>({
    mutationFn: (roomId: number) => apiClient.delete<void>(`/api/chat/rooms/${roomId}`),
    onMutate: async (roomId) => {
      await qc.cancelQueries({ queryKey: CHAT_ROOMS_KEY });
      const previous = qc.getQueryData<InfiniteData<RoomListResponse>>(CHAT_ROOMS_KEY);
      if (previous) {
        qc.setQueryData<InfiniteData<RoomListResponse>>(CHAT_ROOMS_KEY, {
          ...previous,
          pages: previous.pages.map((page) => ({
            ...page,
            rooms: page.rooms.filter((room) => room.roomId !== roomId),
          })),
        });
      }
      return { previous };
    },
    onError: (_err, _roomId, ctx) => {
      if (ctx?.previous) qc.setQueryData(CHAT_ROOMS_KEY, ctx.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: CHAT_ROOMS_KEY });
    },
  });
}
