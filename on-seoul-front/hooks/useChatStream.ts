"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AUTH_LOGOUT_EVENT } from "@/lib/api-client";
import { assertNever } from "@/lib/assert-never";
import { parseSseStream } from "@/lib/sse";
import type { SseEvent } from "@/types/sse-events";

export interface ChatStreamInput {
  question: string;
  roomId?: number;
}

export type ChatStreamState =
  | { phase: "idle" }
  | { phase: "streaming"; content: string; trace: string[] }
  | { phase: "done"; messageId: number; content: string; trace: string[] }
  | { phase: "error"; message: string; content: string; trace: string[] };

export interface UseChatStreamResult {
  state: ChatStreamState;
  send: (input: ChatStreamInput) => Promise<void>;
  cancel: () => void;
  retry: () => Promise<void>;
  reset: () => void;
}

/**
 * SSE 챗봇 스트림 훅. `fetch + ReadableStream` 패턴 사용 (EventSource 금지).
 * - AbortController로 unmount/명시적 취소를 다룬다.
 * - 401 응답 시 `auth:logout` 이벤트 발행 → useAuth가 /login으로 라우팅.
 * - 직전 input을 보관해 `retry()`에서 재사용한다 (스트림 단절 복구용, 절대규칙 B.4).
 */
export function useChatStream(): UseChatStreamResult {
  const [state, setState] = useState<ChatStreamState>({ phase: "idle" });
  const abortRef = useRef<AbortController | null>(null);
  const lastInputRef = useRef<ChatStreamInput | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  const safeSetState = useCallback((next: ChatStreamState) => {
    if (mountedRef.current) setState(next);
  }, []);

  const send = useCallback(
    async (input: ChatStreamInput) => {
      // 진행 중인 스트림이 있으면 취소.
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      lastInputRef.current = input;

      let content = "";
      const trace: string[] = [];
      safeSetState({ phase: "streaming", content, trace: [...trace] });

      try {
        // const res = await fetch("/api/query", {   // Route Handler 경유 (리버스프록시 구성 시 사용)
        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? ""}/query`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
            },
            credentials: "include",
            body: JSON.stringify(input),
            signal: ctrl.signal,
            cache: "no-store",
          },
        );

        if (res.status === 401) {
          if (typeof window !== "undefined") {
            window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
          }
          safeSetState({
            phase: "error",
            message: "세션이 만료되었습니다. 다시 로그인해주세요.",
            content,
            trace: [...trace],
          });
          return;
        }

        if (!res.ok || !res.body) {
          safeSetState({
            phase: "error",
            message: `요청 실패: HTTP ${res.status}`,
            content,
            trace: [...trace],
          });
          return;
        }

        const reader = res.body.getReader();
        for await (const raw of parseSseStream(reader, ctrl.signal)) {
          // 백엔드 events 정본의 mirror 타입(SseEvent)으로 좁힌다.
          const event = raw as SseEvent;
          switch (event.type) {
            case "agent_start":
              trace.push(`▶ ${event.agent}`);
              safeSetState({ phase: "streaming", content, trace: [...trace] });
              break;
            case "tool_call":
              trace.push(`🔧 ${event.tool}`);
              safeSetState({ phase: "streaming", content, trace: [...trace] });
              break;
            case "token":
              // 백엔드가 비정상적으로 비-string delta를 보내면 누적 시 "[object Object]" 등 오염 방지.
              if (typeof event.delta !== "string") break;
              content += event.delta;
              safeSetState({ phase: "streaming", content, trace: [...trace] });
              break;
            case "done":
              safeSetState({
                phase: "done",
                messageId: event.messageId,
                content,
                trace: [...trace],
              });
              return;
            case "error":
              safeSetState({
                phase: "error",
                message: event.message,
                content,
                trace: [...trace],
              });
              return;
            default:
              assertNever(event);
          }
        }

        // 스트림이 done/error 없이 끝났다 — 사용자에게 명시 (절대규칙 B.4).
        safeSetState({
          phase: "error",
          message: "스트리밍이 완료되지 않은 상태에서 연결이 종료되었습니다.",
          content,
          trace: [...trace],
        });
      } catch (e) {
        if (e instanceof Error && e.name === "AbortError") return;
        const message = e instanceof Error ? e.message : "스트림 처리 중 오류가 발생했습니다.";
        safeSetState({ phase: "error", message, content, trace: [...trace] });
      }
    },
    [safeSetState],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retry = useCallback(async () => {
    const last = lastInputRef.current;
    if (!last) return;
    await send(last);
  }, [send]);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    safeSetState({ phase: "idle" });
  }, [safeSetState]);

  return { state, send, cancel, retry, reset };
}
