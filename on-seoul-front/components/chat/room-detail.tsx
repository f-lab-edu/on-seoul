"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ChatConversation } from "@/components/chat/chat-conversation";
import { DeleteRoomDialog } from "@/components/chat/delete-room-dialog";
import { MessageList, type DisplayMessage } from "@/components/chat/message-list";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useChatHistory } from "@/hooks/useChatHistory";
import { useDeleteChatRoom } from "@/hooks/useDeleteChatRoom";
import { ApiError } from "@/lib/api-client";
import { chatHistoryErrorMessage } from "@/lib/api-error-message";

interface RoomDetailProps {
  roomId: number;
}

/**
 * 대화방 화면(`/chat/[roomId]`). 과거 메시지(ASSISTANT는 저장된 service_cards 포함 — 가이드 §3.2)를
 * 시드해 ChatConversation에 넘기고, 같은 roomId로 "이어서 대화하기"를 지원한다.
 * 이력 카드는 실시간 SSE final 카드와 동일 형태이며 동일 컴포넌트(ServiceCardList)로 렌더된다.
 */
export function RoomDetail({ roomId }: RoomDetailProps) {
  const router = useRouter();
  const history = useChatHistory(roomId);
  const deleteRoom = useDeleteChatRoom();
  const [deleteOpen, setDeleteOpen] = useState(false);

  const messages = useMemo<DisplayMessage[]>(() => {
    if (!history.data) return [];
    return history.data.messages.map((m) => ({
      id: `seq-${m.seq}`,
      role: m.role,
      content: m.content,
      // 저장된 시설 카드를 실시간 메시지와 동일 경로로 넘긴다(ServiceCardList 공유 렌더).
      serviceCards: m.service_cards ?? undefined,
    }));
  }, [history.data]);

  // 재진입 시 마지막이 USER인데 ASSISTANT가 아직 없으면 답변 생성 중(백엔드 disconnect 내성).
  // 이 동안 입력을 막고 useChatHistory 폴링으로 답이 채워지길 기다린다(useChatHistory가 자동 폴링).
  const pending =
    messages.length > 0 && messages[messages.length - 1]?.role === "USER";

  function handleConfirmDelete() {
    deleteRoom.mutate(roomId, {
      onSuccess: () => {
        toast.success("대화를 삭제했습니다.");
        router.replace("/chat/history");
      },
      onError: (err) => {
        toast.error(chatHistoryErrorMessage(err));
        setDeleteOpen(false);
      },
    });
  }

  if (history.isLoading) {
    return (
      <section className="flex-1 overflow-y-auto px-4 py-4" role="status" aria-label="대화 불러오는 중">
        <div className="flex flex-col gap-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="ml-auto h-10 w-2/3" />
          <Skeleton className="h-16 w-3/4" />
        </div>
      </section>
    );
  }

  if (history.isError) {
    const notFound = history.error instanceof ApiError && history.error.status === 404;
    return (
      <section className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-10 text-center">
        <p className="text-sm text-muted-foreground">
          {notFound ? "대화방을 찾을 수 없습니다." : chatHistoryErrorMessage(history.error)}
        </p>
        <Link href="/chat/history" className={buttonVariants({ variant: "outline", size: "sm" })}>
          대화 이력으로 돌아가기
        </Link>
      </section>
    );
  }

  return (
    <>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2">
        <Link
          href="/chat/history"
          aria-label="대화 이력으로 돌아가기"
          className="inline-flex min-h-9 min-w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent/50"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
        </Link>
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{history.data?.title}</h2>
        <button
          type="button"
          aria-label="이 대화 삭제"
          onClick={() => setDeleteOpen(true)}
          className="inline-flex min-h-9 min-w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="size-4" aria-hidden="true" />
        </button>
      </div>

      {pending ? (
        // 답변 생성 중 — 읽기 전용. useChatHistory 폴링으로 ASSISTANT가 채워지면 아래 분기로 전환된다.
        <section className="flex-1 overflow-y-auto px-4 py-4">
          <MessageList messages={messages} streamState={{ phase: "idle" }} />
          <div
            role="status"
            aria-live="polite"
            className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"
          >
            <span
              aria-hidden="true"
              className="size-3 rounded-full border-2 border-muted-foreground/30 border-t-foreground motion-safe:animate-spin"
            />
            답변을 작성하고 있어요…
          </div>
        </section>
      ) : (
        <ChatConversation roomId={roomId} initialMessages={messages} />
      )}

      <DeleteRoomDialog
        title={history.data?.title ?? null}
        open={deleteOpen}
        pending={deleteRoom.isPending}
        onOpenChange={(open) => {
          if (!deleteRoom.isPending) setDeleteOpen(open);
        }}
        onConfirm={handleConfirmDelete}
      />
    </>
  );
}
