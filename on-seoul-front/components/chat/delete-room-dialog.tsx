"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface DeleteRoomDialogProps {
  /** 삭제 대상 제목. null이면 닫힌 상태. */
  title: string | null;
  open: boolean;
  pending: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

/**
 * 대화방 삭제 확인 다이얼로그.
 * soft delete지만 UI상 복구 동선이 없으므로 확인 단계를 둔다(가이드 §3.3).
 */
export function DeleteRoomDialog({
  title,
  open,
  pending,
  onOpenChange,
  onConfirm,
}: DeleteRoomDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>대화를 삭제할까요?</DialogTitle>
          <DialogDescription>
            {title ? `"${title}"` : "이 대화"} 삭제 후에는 복구할 수 없습니다.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            취소
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? "삭제 중…" : "삭제"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
