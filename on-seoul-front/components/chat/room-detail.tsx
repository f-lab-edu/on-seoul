"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { ChatInput } from "@/components/chat/chat-input";
import { DeleteRoomDialog } from "@/components/chat/delete-room-dialog";
import { MessageList, type DisplayMessage } from "@/components/chat/message-list";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useChatHistory } from "@/hooks/useChatHistory";
import { useChatStream } from "@/hooks/useChatStream";
import { useDeleteChatRoom } from "@/hooks/useDeleteChatRoom";
import { ApiError } from "@/lib/api-client";
import { chatHistoryErrorMessage } from "@/lib/api-error-message";
import { extractChatContent } from "@/lib/extract-chat-content";

interface RoomDetailProps {
  roomId: number;
}

/**
 * 대화방 상세. 과거 메시지(텍스트만, service_cards 없음 — 가이드 §3.2)를 시드한 뒤,
 * 같은 roomId로 후속 질의를 스트리밍해 "이어서 대화하기"를 지원한다.
 * 실시간 응답의 service_cards는 SSE final에서 오므로 그대로 렌더(이력 카드 미저장과 무관).
 */
export function RoomDetail({ roomId }: RoomDetailProps) {
  const router = useRouter();
  const history = useChatHistory(roomId);
  const { state, send, cancel, retry } = useChatStream();
  const deleteRoom = useDeleteChatRoom();

  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const seededRef = useRef(false);
  const lastCommittedDoneId = useRef<number | null>(null);

  // 이력이 처음 로드되면 messages에 1회 시드. 이후 후속 대화로 누적된 메시지는 보존.
  useEffect(() => {
    if (seededRef.current || !history.data) return;
    seededRef.current = true;
    setMessages(
      history.data.messages.map((m) => ({
        id: `seq-${m.seq}`,
        role: m.role,
        // 백엔드 버그 임시 방어: content에 SSE 스트림 전체가 저장된 경우 answer 필드만 추출.
        // @todo 백엔드 수정 후 extractChatContent 제거.
        content: extractChatContent(m.content),
      })),
    );
  }, [history.data]);

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
    void send({ question, roomId });
  }

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
        <Link
          href="/chat/history"
          className={buttonVariants({ variant: "outline", size: "sm" })}
        >
          대화 이력으로 돌아가기
        </Link>
      </section>
    );
  }

  const streaming = state.phase === "streaming";
  const errored = state.phase === "error";

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
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">
          {history.data?.title}
        </h2>
        <button
          type="button"
          aria-label="이 대화 삭제"
          onClick={() => setDeleteOpen(true)}
          className="inline-flex min-h-9 min-w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="size-4" aria-hidden="true" />
        </button>
      </div>

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

      <ChatInput onSubmit={handleSubmit} onCancel={cancel} streaming={streaming} />

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
