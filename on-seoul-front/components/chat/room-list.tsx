"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { DeleteRoomDialog } from "@/components/chat/delete-room-dialog";
import { RoomItem } from "@/components/chat/room-item";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useChatRooms } from "@/hooks/useChatRooms";
import { useDeleteChatRoom } from "@/hooks/useDeleteChatRoom";
import { chatHistoryErrorMessage } from "@/lib/api-error-message";
import type { RoomSummary } from "@/types/chat-history";

export function RoomList() {
  const {
    data,
    isLoading,
    isError,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useChatRooms();
  const deleteRoom = useDeleteChatRoom();
  const [target, setTarget] = useState<RoomSummary | null>(null);

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const rooms = data?.pages.flatMap((p) => p.rooms) ?? [];

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, rooms.length]);

  function handleConfirmDelete() {
    if (!target) return;
    const roomId = target.roomId;
    deleteRoom.mutate(roomId, {
      onSuccess: () => {
        toast.success("대화를 삭제했습니다.");
        setTarget(null);
      },
      onError: (err) => {
        toast.error(chatHistoryErrorMessage(err));
        setTarget(null);
      },
    });
  }

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3" role="status" aria-label="대화 이력 불러오는 중">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
      >
        <p>대화 이력을 불러오지 못했습니다.</p>
        <div>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            다시 시도
          </Button>
        </div>
      </div>
    );
  }

  if (rooms.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-md border border-dashed border-border px-4 py-10 text-center">
        <p className="text-sm text-muted-foreground">아직 대화 이력이 없습니다.</p>
        <Link href="/chat" className={buttonVariants({ variant: "outline", size: "sm" })}>
          새 채팅 시작하기
        </Link>
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col gap-2">
        {rooms.map((room) => (
          <RoomItem key={room.roomId} room={room} onDelete={setTarget} />
        ))}
        <div ref={sentinelRef} aria-hidden className="h-4" />
        {isFetchingNextPage && (
          <p className="text-center text-xs text-muted-foreground">불러오는 중...</p>
        )}
      </div>

      <DeleteRoomDialog
        title={target?.title ?? null}
        open={target !== null}
        pending={deleteRoom.isPending}
        onOpenChange={(open) => {
          if (!open && !deleteRoom.isPending) setTarget(null);
        }}
        onConfirm={handleConfirmDelete}
      />
    </>
  );
}
