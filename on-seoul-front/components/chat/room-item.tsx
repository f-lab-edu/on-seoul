"use client";

import Link from "next/link";
import { Trash2 } from "lucide-react";

import { formatAbsoluteTime, formatRelativeTime } from "@/lib/relative-time";
import type { RoomSummary } from "@/types/chat-history";

interface RoomItemProps {
  room: RoomSummary;
  onDelete: (room: RoomSummary) => void;
}

/**
 * 대화방 목록 행. 행 본문은 상세로 이동하는 Link이고, 삭제 버튼은 형제 요소로 둔다.
 * (Link 안에 button을 중첩하면 HTML 스펙 위반이므로 분리)
 */
export function RoomItem({ room, onDelete }: RoomItemProps) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-border transition-colors hover:bg-accent/40">
      <Link
        href={`/chat/history/${room.roomId}`}
        className="flex min-w-0 flex-1 flex-col gap-1 px-3 py-3"
      >
        <span className="truncate text-sm font-medium text-foreground">{room.title}</span>
        <time
          dateTime={room.updatedAt}
          title={formatAbsoluteTime(room.updatedAt)}
          className="text-xs text-muted-foreground"
        >
          {formatRelativeTime(room.updatedAt)}
        </time>
      </Link>
      <button
        type="button"
        aria-label={`"${room.title}" 대화 삭제`}
        onClick={() => onDelete(room)}
        className="mr-1 inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}
