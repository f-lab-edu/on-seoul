"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { FilterFields } from "@/components/notifications/filter-fields";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useUpdateSubscription } from "@/hooks/useSubscriptions";
import { notificationErrorMessage } from "@/lib/api-error-message";
import type { Subscription, SubscriptionFilter } from "@/types/notification";

interface Props {
  subscription: Subscription;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SubscriptionEditDialog({ subscription, open, onOpenChange }: Props) {
  const [filter, setFilter] = useState<SubscriptionFilter>(subscription.filter);
  const update = useUpdateSubscription(subscription.id);

  // 다이얼로그가 열릴 때(또는 다른 subscription으로 교체될 때) 폼을 초기화한다.
  useEffect(() => {
    if (open) setFilter(subscription.filter);
  }, [open, subscription.id, subscription.filter]);

  function handleSave() {
    // v1: channels는 항상 EMAIL — 훅 내부에서 강제하지만 명시적 전달.
    update.mutate(
      { filter, channels: ["EMAIL"] },
      {
        onSuccess: () => {
          toast.success("구독을 수정했습니다.");
          onOpenChange(false);
        },
        onError: (err) => {
          toast.error(notificationErrorMessage(err));
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>구독 편집</DialogTitle>
          <DialogDescription>{subscription.serviceName}</DialogDescription>
        </DialogHeader>

        <FilterFields value={filter} onChange={setFilter} />

        <div className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          이메일로 받기
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            취소
          </Button>
          <Button onClick={handleSave} disabled={update.isPending}>
            {update.isPending ? "저장 중..." : "저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
