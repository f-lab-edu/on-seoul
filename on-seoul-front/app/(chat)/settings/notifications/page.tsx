import Link from "next/link";

import { SubscriptionList } from "@/components/notifications/subscription-list";
import { Button } from "@/components/ui/button";

/**
 * 알림 설정 메인.
 * 인증 가드는 상위 `(chat)/layout.tsx`에 위임 — 별도 클라이언트 가드 불필요.
 */
export default function NotificationSettingsPage() {
  return (
    <main className="mx-auto flex h-dvh max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h1 className="text-base font-medium">알림 설정</h1>
        <Link href="/settings/notifications/history">
          <Button variant="outline" size="sm">
            발송 이력
          </Button>
        </Link>
      </header>
      <section className="flex-1 overflow-y-auto px-4 py-4">
        <SubscriptionList />
      </section>
    </main>
  );
}
