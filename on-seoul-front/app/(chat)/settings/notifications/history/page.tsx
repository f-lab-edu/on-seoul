import { DispatchList } from "@/components/notifications/dispatch-list";

/**
 * 알림 발송 이력.
 * 인증 가드와 공통 헤더는 상위 `(chat)/layout.tsx`에 위임.
 */
export default function NotificationHistoryPage() {
  return (
    <section className="flex-1 overflow-y-auto px-4 py-4">
      <DispatchList />
    </section>
  );
}
