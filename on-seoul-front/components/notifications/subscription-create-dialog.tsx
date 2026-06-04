"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  FilterFields,
  hasAtLeastOneCondition,
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
import { useCreateSubscription } from "@/hooks/useSubscriptions";
import { ApiError } from "@/lib/api-client";
import { notificationErrorMessage } from "@/lib/api-error-message";
import type { SubscriptionFilter } from "@/types/notification";

const EMPTY_FILTER: SubscriptionFilter = {};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 새 구독 추가 다이얼로그.
 * 2026-05-28: 구독이 조건 기반으로 전환되어 serviceId 입력 단계가 제거되었다.
 * 명세 §3.3.
 */
export function SubscriptionCreateDialog({ open, onOpenChange }: Props) {
  const [filter, setFilter] = useState<SubscriptionFilter>(EMPTY_FILTER);
  const [formError, setFormError] = useState<string | null>(null);
  const create = useCreateSubscription();

  // 다이얼로그 진입 시 빈 폼으로 초기화. 닫힐 때는 다음 진입을 위해 유지하지 않는다.
  useEffect(() => {
    if (open) {
      setFilter(EMPTY_FILTER);
      setFormError(null);
    }
  }, [open]);

  const canSubmit = hasAtLeastOneCondition(filter) && !create.isPending;

  function handleSubmit() {
    setFormError(null);
    if (!hasAtLeastOneCondition(filter)) {
      setFormError("최소 1개 조건을 선택하세요.");
      return;
    }
    create.mutate(
      { filter, channels: ["EMAIL"] },
      {
        onSuccess: () => {
          toast.success("구독을 추가했습니다.");
          onOpenChange(false);
        },
        onError: (err) => {
          // 400: 빈 조건 / 키워드 3개 초과 / 잘못된 키워드 대상 (명세 §7).
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
          <DialogTitle>새 구독 추가</DialogTitle>
          <DialogDescription>
            관심 있는 조건을 선택하면 매칭되는 공공서비스 변경을 알림으로 받습니다.
          </DialogDescription>
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
            disabled={create.isPending}
          >
            취소
          </Button>
          <div className="flex flex-col items-end gap-1">
            <Button onClick={handleSubmit} disabled={!canSubmit}>
              {create.isPending ? "저장 중..." : "저장"}
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
