"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AUTH_LOGOUT_EVENT } from "@/lib/api-client";
import { parseSseStream } from "@/lib/sse";
import type { ServiceCard } from "@/types/sse-events";

export interface ChatStreamInput {
  question: string;
  roomId?: number;
  // 지도(MAP) 의도용 좌표. 추후 위치 기반 검색 UI 구현 시 채워 보낸다.
  lat?: number;
  lng?: number;
}

/** 스트림 첫머리 `init` 이벤트로 전달되는 방 메타. */
export interface ChatRoomInfo {
  roomId: number;
  created: boolean;
}

export type ChatStreamState =
  | { phase: "idle" }
  | { phase: "streaming"; content: string; trace: string[] }
  | {
      phase: "done";
      messageId: number;
      content: string;
      trace: string[];
      serviceCards: ServiceCard[];
    }
  | { phase: "error"; message: string; content: string; trace: string[] };

export interface UseChatStreamOptions {
  /** `init` 이벤트 수신 시 1회 호출. 페이지가 roomId로 URL 전환/스레딩을 시작한다. */
  onInit?: (info: ChatRoomInfo) => void;
}

export interface UseChatStreamResult {
  state: ChatStreamState;
  send: (input: ChatStreamInput) => Promise<void>;
  cancel: () => void;
  retry: () => Promise<void>;
  reset: () => void;
}

/** 진행(progress) 이벤트의 trace 라벨. `message`가 있으면 그것만 쓴다(특정 type에 의존 X). */
function progressLabel(ev: Record<string, unknown>): string | null {
  return typeof ev.message === "string" && ev.message.length > 0 ? `⏳ ${ev.message}` : null;
}

/**
 * SSE 챗봇 스트림 훅. `fetch + ReadableStream` 패턴 사용 (EventSource 금지).
 * - AbortController로 unmount/명시적 취소를 다룬다.
 * - 401 응답 시 `auth:logout` 이벤트 발행 → useAuth가 /login으로 라우팅.
 * - 직전 input을 보관해 `retry()`에서 재사용한다 (스트림 단절 복구용, 절대규칙 B.4).
 *
 * 이벤트 처리(docs/chat-sse-event-catalog.md):
 * - `init` → onInit 콜백 (종료 아님).
 * - `final`(answer 있고 error 없음) → 종료(done).
 * - `workflow_error`(answer+error) → 종료(error, answer를 사용자 메시지로).
 * - `error`(API 레벨) → 종료(error).
 * - 그 외(step/token 등)는 진행으로 흡수 — 개별 type에 의존하지 않아 forward-compatible.
 */
export function useChatStream(options?: UseChatStreamOptions): UseChatStreamResult {
  const [state, setState] = useState<ChatStreamState>({ phase: "idle" });
  const abortRef = useRef<AbortController | null>(null);
  const lastInputRef = useRef<ChatStreamInput | null>(null);
  const mountedRef = useRef(true);
  // onInit은 매 렌더 최신값을 ref로 들고 있어 send 재생성 없이 호출한다.
  const onInitRef = useRef(options?.onInit);
  onInitRef.current = options?.onInit;

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

      // token 누적을 제거해 content는 항상 ""(스트림 미완료 fallback 표시에만 쓰임).
      const content = "";
      const trace: string[] = [];
      safeSetState({ phase: "streaming", content, trace: [...trace] });

      try {
        // Route Handler 경유 필수 — 직접 백엔드 호출 금지 (CLAUDE.md A.2)
        const res = await fetch("/api/chat/query", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          credentials: "include",
          body: JSON.stringify(input),
          signal: ctrl.signal,
          cache: "no-store",
        });

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
          const ev = raw as Record<string, unknown>;

          // init — 방 메타. 종료 아님.
          if (ev.type === "init") {
            if (typeof ev.room_id === "number") {
              // 직전 input에 roomId를 심어 retry()가 같은 방으로 재전송되게 한다(방 중복 생성 방지).
              if (lastInputRef.current) lastInputRef.current.roomId = ev.room_id;
              onInitRef.current?.({ roomId: ev.room_id, created: ev.created === true });
            }
            continue;
          }

          const hasAnswer = typeof ev.answer === "string";
          const hasError = ev.error != null;

          // final — 정상 종료.
          if (hasAnswer && !hasError) {
            safeSetState({
              phase: "done",
              messageId: typeof ev.message_id === "number" ? ev.message_id : Date.now(),
              content: ev.answer as string,
              trace: [...trace],
              // service_cards는 final에만 존재. 누락 시 빈 배열로 방어.
              serviceCards: Array.isArray(ev.service_cards)
                ? (ev.service_cards as ServiceCard[])
                : [],
            });
            return;
          }

          // workflow_error — answer+error 동반 종료. answer를 사용자 메시지로 노출.
          if (hasAnswer && hasError) {
            safeSetState({
              phase: "error",
              message: ev.answer as string,
              content,
              trace: [...trace],
            });
            return;
          }

          // API 레벨 error — 종료.
          if (ev.type === "error") {
            safeSetState({
              phase: "error",
              message: typeof ev.message === "string" ? ev.message : "오류가 발생했습니다.",
              content,
              trace: [...trace],
            });
            return;
          }

          // 그 외(step 등 미지의 이벤트) — 진행으로 흡수. 개별 type에 의존하지 않는다.
          const label = progressLabel(ev);
          if (label) {
            trace.push(label);
            safeSetState({ phase: "streaming", content, trace: [...trace] });
          }
        }

        // 스트림이 final/error 없이 끝났다 — 사용자에게 명시 (절대규칙 B.4).
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
