"use client";

import { useState } from "react";
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
import { Input } from "@/components/ui/input";
import { useCreateSubscription } from "@/hooks/useSubscriptions";
import { ApiError } from "@/lib/api-client";
import { notificationErrorMessage } from "@/lib/api-error-message";
import type { SubscriptionFilter } from "@/types/notification";

const EMPTY_FILTER: SubscriptionFilter = {
  statuses: [],
  areaNames: [],
  maxClassNames: [],
};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SubscriptionCreateDialog({ open, onOpenChange }: Props) {
  // 서비스 검색 API 경로 미확정 — serviceId 직접 입력으로 임시 처리.
  const [serviceId, setServiceId] = useState("");
  const [filter, setFilter] = useState<SubscriptionFilter>(EMPTY_FILTER);
  const [conflictError, setConflictError] = useState<string | null>(null);
  const create = useCreateSubscription();

  function reset() {
    setServiceId("");
    setFilter(EMPTY_FILTER);
    setConflictError(null);
  }

  function handleOpenChange(next: boolean) {
    if (next) reset();
    onOpenChange(next);
  }

  function handleSubmit() {
    setConflictError(null);
    if (!serviceId.trim()) {
      setConflictError("서비스 ID를 입력해 주세요.");
      return;
    }
    create.mutate(
      { serviceId: serviceId.trim(), filter, channels: ["EMAIL"] },
      {
        onSuccess: () => {
          toast.success("구독을 추가했습니다.");
          onOpenChange(false);
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            if (err.status === 409) {
              setConflictError("이미 구독 중인 서비스입니다.");
              return;
            }
            // 400: serviceId 형식/존재 문제 — toast 대신 필드 인라인 에러로 표시.
            if (err.status === 400) {
              setConflictError(notificationErrorMessage(err));
              return;
            }
          }
          toast.error(notificationErrorMessage(err));
        },
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>새 구독 추가</DialogTitle>
          <DialogDescription>
            서비스 ID와 알림 필터를 설정합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2">
          <label htmlFor="serviceId" className="text-sm font-medium">
            서비스 ID
          </label>
          {/* TODO: 서비스 검색 API 확정 시 검색 컴포넌트로 교체 */}
          <p className="text-xs text-muted-foreground">
            서비스 검색 기능 준비 중입니다. 임시로 서비스 ID를 직접 입력하세요.
          </p>
          <Input
            id="serviceId"
            value={serviceId}
            onChange={(e) => setServiceId(e.target.value)}
            placeholder="예: OA-2269"
            disabled={create.isPending}
            aria-invalid={conflictError ? true : undefined}
            aria-describedby={conflictError ? "serviceId-error" : undefined}
          />
          {conflictError && (
            <p id="serviceId-error" className="text-xs text-destructive" role="alert">
              {conflictError}
            </p>
          )}
        </div>

        <FilterFields value={filter} onChange={setFilter} />

        <div className="rounded-md bg-muted/60 px-3 py-2 text-xs text-muted-foreground">
          이메일로 받기
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={create.isPending}
          >
            취소
          </Button>
          <Button onClick={handleSubmit} disabled={create.isPending}>
            {create.isPending ? "저장 중..." : "저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
