import { notFound } from "next/navigation";

import { RoomDetail } from "@/components/chat/room-detail";

/**
 * 대화방 상세. 인증 가드/공통 헤더는 상위 `(chat)/layout.tsx`에 위임.
 * roomId는 양의 정수만 허용 — 그 외는 404로 보낸다.
 */
export default async function ChatHistoryDetailPage({
  params,
}: {
  params: Promise<{ roomId: string }>;
}) {
  const { roomId } = await params;
  const parsed = Number(roomId);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    notFound();
  }
  return <RoomDetail roomId={parsed} />;
}
