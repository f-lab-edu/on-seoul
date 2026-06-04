import { RoomList } from "@/components/chat/room-list";

/**
 * 대화 이력 목록.
 * 인증 가드와 공통 헤더는 상위 `(chat)/layout.tsx`에 위임.
 */
export default function ChatHistoryPage() {
  return (
    <section className="flex-1 overflow-y-auto px-4 py-4">
      <RoomList />
    </section>
  );
}
