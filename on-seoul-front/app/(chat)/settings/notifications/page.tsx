import { SubscriptionList } from "@/components/notifications/subscription-list";

/**
 * 알림 설정 메인.
 * 인증 가드와 공통 헤더는 상위 `(chat)/layout.tsx`에 위임.
 */
export default function NotificationSettingsPage() {
  return (
    <section className="flex-1 overflow-y-auto px-4 py-4">
      <SubscriptionList />
    </section>
  );
}
