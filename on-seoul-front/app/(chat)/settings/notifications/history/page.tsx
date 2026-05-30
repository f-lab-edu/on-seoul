import Link from "next/link";

import { DispatchList } from "@/components/notifications/dispatch-list";
import { Button } from "@/components/ui/button";

export default function NotificationHistoryPage() {
  return (
    <main className="mx-auto flex h-dvh max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3">
        <h1 className="text-base font-medium">발송 이력</h1>
        <Link href="/settings/notifications">
          <Button variant="outline" size="sm">
            알림 설정
          </Button>
        </Link>
      </header>
      <section className="flex-1 overflow-y-auto px-4 py-4">
        <DispatchList />
      </section>
    </main>
  );
}
