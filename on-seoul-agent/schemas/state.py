from enum import Enum
from typing import Annotated, Any, TypedDict

from schemas.search import ChannelData, search_channels_reducer


def node_path_reducer(
    old: "list[str] | None",
    new: "list[str] | None",
) -> "list[str]":
    """LangGraph reducer for AgentState.node_path — 노드 실행 경로 누적.

    각 노드가 `{"node_path": ["<단계명>"]}` 부분 리스트를 반환하면 전체 경로에
    append 누적된다. search_channels 와 달리 명시적 리셋(sentinel)을 두지 않는다 —
    node_path 는 self-correction 재시도를 포함한 전체 실행 경로 관측이 목적이므로
    재시도 시에도 리셋하지 않고 누적을 유지한다(예: sql_node → retry_prep → vector_node).

    None / 빈 리스트는 no-op(기존 누적 유지).
    """
    if not new:
        return old or []
    return (old or []) + new


def dict_merge_reducer(
    old: "dict[str, Any] | None",
    new: "dict[str, Any] | None",
) -> "dict[str, Any]":
    """부분 dict 업데이트를 얕게 병합한다.

    filters·emit·plan 처럼 여러 노드가 서로 다른 키를 다른 super-step 에 기록하는
    채널에 적용한다. new 가 비면(no-op) 기존 누적 유지.
    값을 지우려면 명시적으로 그 키에 None 을 보낸다(머지 → None 으로 덮임).
    """
    if not new:
        return old or {}
    return {**(old or {}), **new}


class IntentType(str, Enum):
    SQL_SEARCH = "SQL_SEARCH"
    VECTOR_SEARCH = "VECTOR_SEARCH"
    MAP = "MAP"
    ANALYTICS = "ANALYTICS"
    FALLBACK = "FALLBACK"


class ActionType(str, Enum):
    """TriageAgent가 결정하는 행동 유형 (2축 분리의 action 축)."""

    RETRIEVE = "RETRIEVE"
    DIRECT_ANSWER = "DIRECT_ANSWER"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    EXPLAIN = "EXPLAIN"


# =============================================================================
# 도메인/단계 working state — 중첩 서브 채널 (total=False, leaf .get())
# =============================================================================


class TriageState(TypedDict, total=False):
    """TriageAgent 산출 — triage_node 가 통째 set."""

    action: "ActionType | None"
    out_of_scope_type: str | None
    user_rationale: str | None


class PlanState(TypedDict, total=False):
    """검색 계획 — dict_merge 채널.

    router forced 경로는 intent 만 쓰므로 vector_sub_intent/secondary_intent 가
    머지로 보존되어야 한다(평면 sticky 동등성, 행동 무변경 핵심).
    """

    intent: "IntentType | None"
    refined_query: str | None
    vector_sub_intent: str | None
    secondary_intent: "IntentType | None"


class FilterState(TypedDict, total=False):
    """post-filter — dict_merge 채널 (retry_prep 부분 드롭)."""

    max_class_name: str | None
    area_name: str | None
    service_status: str | None
    payment_type: str | None


class SqlState(TypedDict, total=False):
    """SQL_SEARCH 결과 — sql_node 단일 소유."""

    results: "list[dict[str, Any]] | None"
    keyword: str | None


class VectorState(TypedDict, total=False):
    """VECTOR_SEARCH 결과 (메타데이터 only) — vector_node 단일 소유."""

    results: "list[dict[str, Any]] | None"


class MapState(TypedDict, total=False):
    """MAP GeoJSON 결과 — map_node 단일 소유."""

    results: "dict[str, Any] | None"


class AnalyticsState(TypedDict, total=False):
    """ANALYTICS 집계 결과 — analytics_node 단일 소유."""

    results: "list[dict[str, Any]] | None"
    group_by: str | None
    metric: str | None
    keyword: str | None


class HydrationState(TypedDict, total=False):
    """검색 결과 → 원본 통합 슬롯 — hydration_node/rehydrate_node wholesale."""

    hydrated_services: "list[dict[str, Any]] | None"


class OutputState(TypedDict, total=False):
    """최종 답변 산출 — answer/describe/direct/ambiguous/explain/out_of_scope wholesale."""

    answer: str | None
    title: str | None
    service_cards: "list[dict[str, Any]] | None"


class EmitState(TypedDict, total=False):
    """SSE emit-once 가드 — dict_merge 채널 (노드별 부분 bool)."""

    decision_emitted: bool
    searching_emitted: bool
    answering_emitted: bool


class AgentState(TypedDict):
    # ── 보편 입력 (평면) ──
    room_id: int
    message_id: int
    message: str  # 사용자 원본 질문
    title_needed: bool  # 제목 생성 필요 여부
    # MAP intent 반경 검색용 좌표 (ChatRequest.lat/lng 로부터 주입).
    # None이면 MAP intent를 FALLBACK으로 대체한다.
    user_lat: float | None  # 클라이언트 위도 (latitude)
    user_lng: float | None  # 클라이언트 경도 (longitude)
    # API 서비스가 chat_messages에서 조립한 직전 N턴 대화 이력.
    history: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": str}, ...]
    # ── carryover 입력 (평면) ──
    # 직전 턴의 결과 엔티티(정체성). 각 항목 {"service_id": str, "label": str}.
    prev_entities: list[dict[str, str]] | None
    # 직전 턴의 분류 intent. 현재는 carryover 슬롯으로만 보관(소비 경로 없음).
    prev_intent: IntentType | None
    # 직전 턴의 판단 근거(user_rationale). EXPLAIN action 이 소비한다.
    prev_reasoning: str | None
    # 참조 해소 결과: 현재 message 가 지시 참조일 때 바인딩된 service_id 리스트.
    target_service_ids: list[str] | None
    # ── 재시도 제어 (평면) ──
    # LangGraph 자기 교정(Self-Correction) 루프 카운터.
    retry_count: int  # 재시도 횟수 (0 = 아직 재시도 없음)
    # 하드 필터 0건으로 인한 완화 재시도 신호. AnswerAgent 가 답변에 명시한다.
    retry_relaxed: bool
    # 완화 재시도 시 retry_prep_node 가 드롭한 필터 키 목록(M1-b).
    # AnswerAgent 가 사용자 라벨로 변환해 "무엇을 완화했는지" 답변에 명시한다.
    relaxed_filters: list[str] | None
    # 방향성 재시도: retry_prep_node 가 다음 순회의 intent 를 강제할 때 세팅(1회성).
    forced_intent: IntentType | None
    # MAP 0건 재시도 시 확장 반경(m). 없으면 기본 반경(1000m) 적용.
    retry_radius_m: int | None
    # ── 오류/캐시 (평면) ──
    error: str | None  # 오류 메시지 (있을 경우)
    cache_hit: bool  # cache_check_node 결과 (기본값 False)
    # singleflight 락 키 — cache_check_node 가 락을 획득한 패스에서 기록한다.
    # 락 해제(retry_prep / cache_write)가 획득 시점과 동일 키를 쓰도록 보관해
    # C2 0건 게이트(cache_write 우회) 경로에서도 락이 누수되지 않게 한다.
    answer_lock_key: str | None
    # ── 인프라/관측 (평면) ──
    # 노드 실행 경로 누적 (관측용). node_path_reducer 가 부분 리스트를 append 병합한다.
    node_path: Annotated[list[str], node_path_reducer]
    # 검색 채널 관측 (chat_search_queries / chat_search_results 적재용).
    search_channels: Annotated[dict[str, ChannelData], search_channels_reducer]
    trace: dict[str, Any] | None  # LangGraph 실행 메타데이터
    started_at: float | None  # 그래프 실행 시작 시각 (time.monotonic())
    # ── fusion 신호 (평면) ──
    # rrf_fusion_node 가 SQL+VECTOR 팬아웃 결과를 RRF 통합한 service_id 순서 리스트.
    rrf_merged_ids: list[str] | None
    # ── 도메인 working state (중첩) ──
    triage: TriageState
    plan: Annotated[PlanState, dict_merge_reducer]
    filters: Annotated[FilterState, dict_merge_reducer]
    sql: SqlState
    vector: VectorState
    map: MapState
    analytics: AnalyticsState
    hydration: HydrationState
    output: OutputState
    emit: Annotated[EmitState, dict_merge_reducer]
