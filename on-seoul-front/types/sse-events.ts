/**
 * SSE 이벤트 타입 — 백엔드 on-seoul-agent/schemas/events.py 의 미러.
 * 백엔드 스키마가 변경되면 이 파일을 동기화한다. 어긋나면 frontend-qa가 차단.
 *
 * 정본: on-seoul-agent/schemas/events.py
 *
 * 백엔드는 두 가지 discriminant를 혼용한다:
 *   - `step` : 진행 상태 메시지 (예: routing, searching …)
 *   - `type` : 토큰·완료·에러 등 구조적 이벤트
 */

export type AgentName = "router" | "sql" | "vector" | "answer";

export type ToolName = "sql_search" | "vector_search" | "map_search";

/** 백엔드가 step 필드로 보내는 진행 상태 메시지. */
export type SseProgressEvent = { step: string; message: string };

/** 백엔드가 type 필드로 보내는 구조적 이벤트. */
export type SseTypedEvent =
  | { type: "agent_start"; agent: AgentName }
  | { type: "tool_call"; tool: ToolName; args: unknown }
  | { type: "token"; delta: string }
  | { type: "done"; messageId: number }
  | { type: "final"; message_id: number; answer: string }
  | { type: "workflow_error"; message_id: number; answer: string; error: string }
  | { type: "error"; message: string };

export type SseEvent = SseProgressEvent | SseTypedEvent;

/** step 기반 이벤트인지 판별하는 타입 가드. */
export function isSseProgressEvent(e: SseEvent): e is SseProgressEvent {
  return "step" in e;
}
