"use client";

import { useState } from "react";

import { SubscriptionCard } from "@/components/notifications/subscription-card";
import { SubscriptionCreateDialog } from "@/components/notifications/subscription-create-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSubscriptions } from "@/hooks/useSubscriptions";

export function SubscriptionList() {
  const [createOpen, setCreateOpen] = useState(false);
  const { data, isLoading, isError, refetch } = useSubscriptions();

  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          새 구독 추가
        </Button>
      </div>

      {isLoading && (
        <div className="flex flex-col gap-3" role="status" aria-label="구독 목록 불러오는 중">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      )}

      {isError && (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
        >
          <p>구독 목록을 불러오지 못했습니다.</p>
          <div>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              다시 시도
            </Button>
          </div>
        </div>
      )}

      {!isLoading && !isError && data && data.length === 0 && (
        <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          구독 중인 알림이 없습니다. 새 구독을 추가해보세요.
        </p>
      )}

      {!isLoading && data && data.length > 0 && (
        <div className="flex flex-col gap-3">
          {data.map((sub) => (
            <SubscriptionCard key={sub.id} subscription={sub} />
          ))}
        </div>
      )}

      <SubscriptionCreateDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}
