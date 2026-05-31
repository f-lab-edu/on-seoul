"use client";

import { useEffect, useRef } from "react";

import { DispatchItem } from "@/components/notifications/dispatch-item";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDispatches } from "@/hooks/useDispatches";

export function DispatchList() {
  const {
    data,
    isLoading,
    isError,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useDispatches();

  const sentinelRef = useRef<HTMLDivElement | null>(null);

  // v1: SMS 이력이 응답에 포함되어도 표시하지 않음. 현재 응답에 channel 필드가 없으므로
  // 별도 필터 없이 EMAIL 기준으로 렌더한다. (v2에서 channel 필드 추가 시 필터 도입)
  const items = data?.pages.flatMap((p) => p.dispatches) ?? [];

  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage, items.length]);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-3" role="status" aria-label="발송 이력 불러오는 중">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
      >
        <p>발송 이력을 불러오지 못했습니다.</p>
        <div>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            다시 시도
          </Button>
        </div>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        발송된 알림 이력이 없습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {items.map((d) => (
        <DispatchItem key={d.id} dispatch={d} />
      ))}
      <div ref={sentinelRef} aria-hidden className="h-4" />
      {isFetchingNextPage && (
        <p className="text-center text-xs text-muted-foreground">불러오는 중...</p>
      )}
    </div>
  );
}
