"use client";

import { useState } from "react";
import { toast } from "sonner";

import { SubscriptionEditDialog } from "@/components/notifications/subscription-edit-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDeleteSubscription } from "@/hooks/useSubscriptions";
import { notificationErrorMessage } from "@/lib/api-error-message";
import { formatAbsoluteTime, formatRelativeTime } from "@/lib/relative-time";
import { STATUS_LABEL, type Subscription } from "@/types/notification";

function summarizeFilter(sub: Subscription): string {
  const parts: string[] = [];
  if (sub.filter.areaNames.length > 0) parts.push(sub.filter.areaNames.join(", "));
  if (sub.filter.maxClassNames.length > 0)
    parts.push(sub.filter.maxClassNames.join(", "));
  if (sub.filter.statuses.length > 0)
    parts.push(sub.filter.statuses.map((s) => STATUS_LABEL[s]).join(", "));
  return parts.length === 0 ? "모든 변경 알림" : parts.join(" · ");
}

export function SubscriptionCard({ subscription }: { subscription: Subscription }) {
  const [editOpen, setEditOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const del = useDeleteSubscription(subscription.id);

  function handleDelete() {
    del.mutate(undefined, {
      onSuccess: () => {
        toast.success("구독을 해지했습니다.");
        setConfirmOpen(false);
      },
      onError: (err) => {
        toast.error(notificationErrorMessage(err));
      },
    });
  }

  const lastNotified = subscription.lastNotifiedAt;

  return (
    <article className="flex flex-col gap-2 rounded-lg border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <h3 className="text-sm font-medium">{subscription.serviceName}</h3>
          <p className="text-xs text-muted-foreground">{summarizeFilter(subscription)}</p>
        </div>
        <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
          EMAIL
        </span>
      </header>

      <p className="text-xs text-muted-foreground">
        {lastNotified ? (
          <span title={formatAbsoluteTime(lastNotified)}>
            마지막 발송: {formatRelativeTime(lastNotified)}
          </span>
        ) : (
          "발송 이력 없음"
        )}
      </p>

      <footer className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
          편집
        </Button>
        <Button
          variant="destructive"
          size="sm"
          onClick={() => setConfirmOpen(true)}
        >
          해지
        </Button>
      </footer>

      <SubscriptionEditDialog
        subscription={subscription}
        open={editOpen}
        onOpenChange={setEditOpen}
      />

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>구독을 해지하시겠습니까?</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {subscription.serviceName} 알림을 더 이상 받지 않습니다.
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmOpen(false)}
              disabled={del.isPending}
            >
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={del.isPending}
            >
              {del.isPending ? "해지 중..." : "해지"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </article>
  );
}
