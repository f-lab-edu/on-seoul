from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class EventType(str, Enum):
    AGENT_START = "agent_start"
    TOOL_CALL = "tool_call"
    TOKEN = "token"
    DONE = "done"
    ERROR = "error"


class SSEEvent(BaseModel):
    event: EventType
    data: Any = None
    message_id: int | None = None


class SourceEntry(BaseModel):
    """검색 채널 1개의 hits 요약."""

    channel: str
    hits: int


class DecisionEvent(BaseModel):
    """Triage 완료 직후 SSE로 방출되는 판단 근거 이벤트.

    action    — TriageAgent가 결정한 행동 유형 (RETRIEVE|DIRECT_ANSWER|AMBIGUOUS|OUT_OF_SCOPE|EXPLAIN)
    routes    — [primary_intent, secondary_intent] 에서 None 제거한 리스트. RETRIEVE 외에는 [].
    user_rationale — TriageAgent가 산출한 사용자용 근거 1문장 (최대 200자).
    sources   — 검색 전 단계라 항상 []. 검색 완료 후 hits는 별도 SourcesUpdateEvent로 방출된다.
    """

    event: Literal["decision"] = "decision"
    action: str
    routes: list[str]
    user_rationale: str
    sources: list[dict[str, Any]] = []


class CriticDecisionEvent(BaseModel):
    """Retrieval-critic 라운드 결정 직후 SSE로 방출되는 판단 근거 이벤트 (L1 Phase 5).

    검색 결과가 약할 때(0건/thin/skew) critic LLM 이 다음 행동을 정하면 그 근거를
    사용자에게 투명하게 노출한다. 기존 triage `decision` 이벤트(DecisionEvent)와
    동일한 "판단 투명성" 철학을 따르되, 단일 실행 1회인 triage decision 과 달리
    critic 은 최대 N회 돌 수 있어(round) 별개 이벤트 타입으로 분리한다 —
    두 이벤트가 같은 `decision` 프레임에서 서로 덮어쓰지 않게 한다.

    decision       — critic 3택 (ANSWER|REPLAN|STOP).
    round          — critic 라운드 인덱스(0-base). retry_count 와 정렬된다.
    user_rationale — critic 근거 1문장. sanitize_user_rationale 로 내부 식별자 제거.
    """

    event: Literal["critic_decision"] = "critic_decision"
    decision: str
    round: int
    user_rationale: str


class SourcesUpdateEvent(BaseModel):
    """검색 완료 후 decision sources 채움 이벤트.

    graph.stream()이 yield "result" 직전에 emit한다.
    sql_results / vector_results / map_results / analytics_results 각 채널의
    실제 hits 수를 담는다. 빈 채널(None 또는 빈 리스트)은 포함하지 않는다.
    """

    event: Literal["sources_update"] = "sources_update"
    sources: list[SourceEntry]
