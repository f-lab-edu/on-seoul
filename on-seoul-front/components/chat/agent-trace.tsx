import { cn } from "@/lib/utils";

interface AgentTraceProps {
  trace: string[];
  className?: string;
}

/**
 * 스트리밍 중 진행 상황을 표시. 비어 있으면 렌더하지 않는다.
 * 순수 표현 컴포넌트 — 'use client' 불필요.
 */
export function AgentTrace({ trace, className }: AgentTraceProps) {
  if (trace.length === 0) return null;
  return (
    <ul
      className={cn(
        "flex flex-col gap-1 rounded-md border border-border/60 bg-muted/40 px-3 py-2 text-xs text-muted-foreground",
        className,
      )}
      aria-label="에이전트 진행 상황"
    >
      {trace.map((line, idx) => (
        <li key={`${idx}-${line}`} className="font-mono">
          {line}
        </li>
      ))}
    </ul>
  );
}
