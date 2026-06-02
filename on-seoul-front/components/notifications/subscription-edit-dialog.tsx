"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  FilterFields,
  hasAtLeastOneCondition,
  summarizeFilter,
} from "@/components/notifications/filter-fields";
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
import { ApiError } from "@/lib/api-client";
import { notificationErrorMessage } from "@/lib/api-error-message";
import type { Subscription, SubscriptionFilter } from "@/types/notification";

interface Props {
  subscription: Subscription;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SubscriptionEditDialog({ subscription, open, onOpenChange }: Props) {
  const [filter, setFilter] = useState<SubscriptionFilter>(subscription.filter);
  const [formError, setFormError] = useState<string | null>(null);
  const update = useUpdateSubscription(subscription.id);

  // 다이얼로그가 열릴 때(또는 다른 subscription으로 교체될 때) 폼을 초기화한다.
  useEffect(() => {
    if (open) {
      setFilter(subscription.filter);
      setFormError(null);
    }
  }, [open, subscription.id, subscription.filter]);

  const canSubmit = hasAtLeastOneCondition(filter) && !update.isPending;

  function handleSave() {
    setFormError(null);
    if (!hasAtLeastOneCondition(filter)) {
      setFormError("최소 1개 조건을 선택하세요.");
      return;
    }
    // v1: channels는 항상 EMAIL — 훅 내부에서 강제하지만 명시적 전달.
    update.mutate(
      { filter, channels: ["EMAIL"] },
      {
        onSuccess: () => {
          toast.success("구독을 수정했습니다.");
          onOpenChange(false);
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 400) {
            setFormError(notificationErrorMessage(err));
            return;
          }
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
          <DialogDescription>{summarizeFilter(subscription.filter)}</DialogDescription>
        </DialogHeader>

        <FilterFields value={filter} onChange={setFilter} />

        <div className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          이메일로 받기
        </div>

        {formError && (
          <p className="text-xs text-destructive" role="alert">
            {formError}
          </p>
        )}

        <DialogFooter className="gap-1 sm:gap-1">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            취소
          </Button>
          <div className="flex flex-col items-end gap-1">
            <Button onClick={handleSave} disabled={!canSubmit}>
              {update.isPending ? "저장 중..." : "저장"}
            </Button>
            {!hasAtLeastOneCondition(filter) && (
              <span className="text-[11px] text-muted-foreground">
                최소 1개 조건을 선택하세요
              </span>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
