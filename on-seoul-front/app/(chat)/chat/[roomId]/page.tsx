import { notFound } from "next/navigation";

import { RoomDetail } from "@/components/chat/room-detail";

/**
 * 대화방 화면. `/chat`에서 첫 질의로 생성된 방(URL 교체), 이력 목록 진입, 새로고침/딥링크가 모두 여기로 온다.
 * roomId는 양의 정수만 허용 — 그 외는 404. 인증 가드/공통 헤더는 상위 (chat)/layout.tsx에 위임.
 */
export default async function ChatRoomPage({
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
