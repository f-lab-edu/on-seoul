/**
 * SSE 이벤트 타입 — 백엔드 정본 미러.
 * 백엔드 스키마가 변경되면 이 파일을 동기화한다. 어긋나면 frontend-qa가 차단.
 *
 * 정본:
 *   - on-seoul-agent/schemas/events.py (이벤트 타입)
 *   - on-seoul-agent/schemas/state.py AgentState.service_cards (ServiceCard 필드)
 *   - on-seoul-agent/agents/answer_agent.py _normalize() (ServiceCard 정규화)
 *
 * 백엔드는 두 가지 discriminant를 혼용한다:
 *   - `step` : 진행 상태 메시지 (예: routing, searching …)
 *   - `type` : 토큰·완료·에러 등 구조적 이벤트
 */

export type AgentName = "router" | "sql" | "vector" | "answer";

export type ToolName = "sql_search" | "vector_search" | "map_search";

/** 백엔드가 step 필드로 보내는 진행 상태 메시지. */
export type SseProgressEvent = { step: string; message: string };

/**
 * 시설/서비스 카드 — `final` 이벤트의 `service_cards` 배열 원소.
 * null 필드는 카드 렌더링에서 해당 라인을 생략 (docs/chat-service-cards-interface.md §2.2).
 */
export type ServiceCard = {
  service_id: string;
  service_name: string | null;
  area_name: string | null;
  place_name: string | null;
  max_class_name: string | null;
  min_class_name: string | null;
  service_status: string | null;
  payment_type: string | null;
  target_info: string | null;
  receipt_start_dt: string | null;
  receipt_end_dt: string | null;
  service_url: string;
};

/** 백엔드가 type 필드로 보내는 구조적 이벤트. */
export type SseTypedEvent =
  | { type: "agent_start"; agent: AgentName }
  | { type: "tool_call"; tool: ToolName; args: unknown }
  | { type: "token"; delta: string }
  | { type: "done"; messageId: number }
  | {
      type: "final";
      message_id: number;
      answer: string;
      intent: "SQL_SEARCH" | "VECTOR_SEARCH" | "MAP" | "FALLBACK" | null;
      title: string | null;
      cache_hit: boolean;
      service_cards: ServiceCard[];
    }
  | {
      type: "workflow_error";
      message_id: number;
      answer: string;
      error: string;
      intent?: string;
      title?: string;
      // 백엔드가 service_cards 를 함께 보낼 수 있으나 §8에 따라 무시 — 타입에 포함하지 않음.
    }
  | { type: "error"; message: string };

export type SseEvent = SseProgressEvent | SseTypedEvent;

/** step 기반 이벤트인지 판별하는 타입 가드. */
export function isSseProgressEvent(e: SseEvent): e is SseProgressEvent {
  return "step" in e;
}
