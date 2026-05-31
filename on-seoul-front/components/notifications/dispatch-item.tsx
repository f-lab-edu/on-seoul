"use client";

import { formatAbsoluteTime, formatRelativeTime } from "@/lib/relative-time";
import type { Dispatch } from "@/types/notification";

function StatusBadge({ status }: { status: Dispatch["status"] }) {
  if (status === "SUCCESS") {
    return (
      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
        성공
      </span>
    );
  }
  return (
    <span className="rounded-full bg-destructive/10 px-2 py-0.5 text-[10px] font-medium text-destructive">
      실패
    </span>
  );
}

export function DispatchItem({ dispatch }: { dispatch: Dispatch }) {
  // body 첫 2줄 요약.
  const summary = dispatch.body.split("\n").slice(0, 2).join("\n");

  return (
    <article className="flex flex-col gap-1 rounded-lg border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-sm font-medium">{dispatch.title}</h3>
        </div>
        <StatusBadge status={dispatch.status} />
      </header>

      <p className="line-clamp-2 whitespace-pre-line text-xs text-foreground/80">
        {summary}
      </p>

      <p className="text-xs text-muted-foreground" title={formatAbsoluteTime(dispatch.sentAt)}>
        {formatRelativeTime(dispatch.sentAt)}
      </p>
    </article>
  );
}
