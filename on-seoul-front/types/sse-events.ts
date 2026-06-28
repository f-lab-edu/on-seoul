/**
 * Chat SSE 이벤트 타입 — 정본 미러.
 * 정본: on-seoul-front/docs/chat-sse-event-catalog.md
 *   (API 서비스 `ChatController` 가 프론트로 내보내는 SSE 스트림 카탈로그)
 *   - init/error: API 서비스 소유(named 이벤트)
 *   - step/final/workflow_error: AI 서비스(on-seoul-agent) 소유, API가 name 없는 data로 relay
 *
 * 식별 규칙(카탈로그 §4):
 *   - name 없는 data 이벤트는 payload 키로 구분한다.
 *   - `answer` 있고 `error` 없음 → final
 *   - `answer` + `error` → workflow_error (종료)
 *   - 그 외(step 등) → 진행(progress)
 *   개별 type 이름에 의존하지 않으므로 미등재/신규 이벤트도 진행으로 안전하게 흡수된다.
 */

export type AgentIntent = "SQL_SEARCH" | "VECTOR_SEARCH" | "MAP" | "FALLBACK" | null;

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

/**
 * API 서비스가 스트림 첫머리에 1회 emit하는 방 메타 (`event:init`).
 * 프론트는 room_id로 URL 전환/스레딩을 즉시 시작한다(답변 완료 대기 X).
 */
export type SseInitEvent = { type: "init"; room_id: number; created: boolean };

/** AI 서비스 진행 메시지(name 없는 data, `step` 키 보유). */
export type SseProgressEvent = { step: string; message: string };

/** 정상 종료 — `answer` 있고 `error` 없음. (title은 별도 `title` 이벤트로 분리됨) */
export type SseFinalEvent = {
  type: "final";
  message_id: number;
  answer: string;
  intent: AgentIntent;
  cache_hit: boolean;
  service_cards: ServiceCard[];
};

/**
 * 대화 제목 이벤트 — 신규 방 첫 턴에만 발행. `final`과 순서 무관하게(먼저/나중) 도착하며,
 * 제목 생성 실패 시 아예 오지 않을 수 있다(fail-open). `init` 이후 도착이 보장된다.
 */
export type SseTitleEvent = {
  type: "title";
  room_id: number;
  title: string;
  message_id: number;
  query: string;
};

/** 워크플로우 오류 — `answer`와 `error`를 함께 가진 종료 이벤트(이력 저장 제외). */
export type SseWorkflowErrorEvent = {
  type: "workflow_error";
  answer: string;
  error: string;
};

/**
 * API 서비스 레벨 오류(`event:error`).
 * data는 평문 문자열이며 파서가 `message`로 래핑한다(카탈로그 §5).
 */
export type SseErrorEvent = { type: "error"; message: string };

export type SseEvent =
  | SseInitEvent
  | SseProgressEvent
  | SseTitleEvent
  | SseFinalEvent
  | SseWorkflowErrorEvent
  | SseErrorEvent;

/** step 기반 진행 이벤트인지 판별. */
export function isSseProgressEvent(e: { step?: unknown }): e is SseProgressEvent {
  return typeof e.step === "string";
}
