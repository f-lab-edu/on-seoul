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
    """입구 분류 산출 — intake_node 가 통째 set (구 triage_node 채널).

    turn_kind: intake 가 산출하는 턴 성격(= 기존 follow_up_type). REFINE/DRILL/
    RELEVANCE/META/NEW. route_intake 가 1차 분기에 사용한다.
    """

    action: "ActionType | None"
    out_of_scope_type: str | None
    user_rationale: str | None
    turn_kind: str | None


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

    # 다중 카테고리 필터 — SQL 은 max_class_name = ANY(:classes), gate 는 멤버십 매칭.
    # "체육시설 말고" 같은 제외 표현은 여집합(닫힌 5종 − X)을 리스트로 담는다.
    # 단일 카테고리도 리스트로 담는다(["체육시설"]). None/[] 이면 미적용(area_name 정합).
    max_class_name: list[str] | None
    # 다중 지역 필터 — SQL 은 area_name = ANY(:areas), gate 는 교집합 매칭.
    # 단일 지역도 리스트로 담는다(["강남구"]). None/[] 이면 미적용.
    area_name: list[str] | None
    service_status: str | None
    payment_type: str | None
    # 대상 그룹 필터 — CHILD/ADULT/SENIOR/FAMILY. tools.target_audience 토큰맵 소비.
    target_audience: str | None


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
    """최종 답변 산출 — answer/describe/direct/ambiguous/explain/out_of_scope wholesale.

    제목(title)은 별도 채널(generate_title_node)로 분리되어 이 산출에 포함되지 않는다.
    """

    answer: str | None
    service_cards: "list[dict[str, Any]] | None"


class EmitState(TypedDict, total=False):
    """SSE emit-once 가드 — dict_merge 채널 (노드별 부분 bool)."""

    decision_emitted: bool
    searching_emitted: bool
    answering_emitted: bool


class PrevWorkingSet(TypedDict, total=False):
    """직전 턴 대화 워킹셋 — 입력 전용 중첩 채널(그래프 내 갱신 없음, 리듀서 불필요).

    "검색 구성"이지 "결과 스냅샷"이 아니다 — 후속은 검색 구성에 제약을 더해 재검색한다
    (carryover 철학: 정체성만 운반, 사실은 rehydrate 재조회).

    entities: 직전 노출 결과 [{service_id, label}, ...] (= 기존 prev_entities).
    intent:   직전 분류 intent (REFINE 재검색의 forced_intent 입력).
    reasoning: 직전 판단 근거(= 기존 prev_reasoning). META 가 소비.
    refined_query: 직전 정제 질의.
    applied_filters: effective(완화 후) 필터 — 후속이 올바른 베이스에 얹히도록.
    relaxed/relaxed_filters: 직전 결과가 자동 완화로 나왔는지 + 드롭된 항목.
    """

    entities: "list[dict[str, str]] | None"
    intent: "IntentType | None"
    reasoning: str | None
    refined_query: str | None
    applied_filters: "dict[str, str | list[str] | None] | None"
    relaxed: bool
    relaxed_filters: "list[str] | None"


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
    # 대화 워킹셋 — 직전 검색 구성. 입력 전용 중첩 채널(리듀서 없음).
    # 신규 채널 우선, 미전송 시 평면 슬롯(prev_entities/prev_intent/prev_reasoning)
    # 으로 폴백한다(routers/chat.py 조립). 첫 턴/구 클라이언트면 전부 None(하위호환).
    prev_working_set: "PrevWorkingSet | None"
    # 참조 해소 결과: 현재 message 가 지시 참조일 때 바인딩된 service_id 리스트.
    target_service_ids: list[str] | None
    # ── 재시도 제어 (평면) ──
    # 단일 retrieval 예산 카운터. self_correction(빈 답변/0건)과 L1 retrieval-critic
    # (0건·thin·skew)이 *공유*한다 — 별도 카운터를 두지 않아 예산 이중 카운트를 막는다.
    # 캡 단일 출처는 Settings.max_retrieval_retries(기본 2). 도달 시 하드 백스톱.
    retry_count: int  # 재시도 횟수 (0 = 아직 재시도 없음)
    # 하드 필터 0건으로 인한 완화 재시도 신호. AnswerAgent 가 답변에 명시한다.
    retry_relaxed: bool
    # 완화 재시도 시 retry_prep_node 가 드롭한 필터 키 목록.
    # AnswerAgent 가 사용자 라벨로 변환해 "무엇을 완화했는지" 답변에 명시한다.
    relaxed_filters: list[str] | None
    # 완화로 드롭된 필터의 *원래 값*(키→값). 큐레이션이 의도 제약을 복원할 때 쓴다 —
    # filters 채널은 드롭 시 값을 None 으로 비우므로 원 요청값은 여기서만 복원 가능하다.
    # retry_prep_node 가 드롭 시점에 적재한다. 완화가 없으면 None(하위호환). 리듀서 불필요.
    relaxed_values: "dict[str, str | None] | None"
    # 방향성 재시도: retry_prep_node 가 다음 순회의 intent 를 강제할 때 세팅(1회성).
    forced_intent: IntentType | None
    # MAP 0건 재시도 시 확장 반경(m). 없으면 기본 반경(1000m) 적용.
    retry_radius_m: int | None
    # ── L1 retrieval-critic 판단 (평면) ──
    # retrieval_critic_node 가 검색 결과를 보고 정하는 다음 행동. 세 슬롯 모두
    # None = critic 미진입(명백히 좋은 80% 경로 / critic 실패 fail-open). 스캐폴딩
    # 단계라 아직 소비하는 노드/엣지가 없다 — 값은 항상 None(회귀 0).
    # critic_decision:    "ANSWER"/"REPLAN"/"STOP" (CriticDecision.value).
    # critic_replan_hint: REPLAN 시 재탐색 방향(ReplanHint.model_dump()). retry_prep 가 소비.
    # critic_rationale:   decision 이벤트용 근거 1문장(내부 식별자 제거 후).
    critic_decision: str | None
    critic_replan_hint: "dict[str, Any] | None"
    critic_rationale: str | None
    # ── 결과 품질 자각 패스 ──
    # pre_answer_gate_node 가 RETRIEVE 경로에서 산출. answer 가 소비해 톤/제안 조정.
    # 쏠림/빈약 휴리스틱 결과(예: {"skew_field","skew_value","skew_ratio","thin"})
    # 또는 점검할 게 없거나 실패 시 None(현행 조립 그대로, 완전 하위호환). 리듀서 불필요.
    result_quality: "dict[str, Any] | None"
    # 직전 assistant 발화에 통합회원 안내가 이미 나갔는지(상류 history 파싱 결과).
    # answer 는 raw history 가 아니라 이 bool 만 소비한다(책임 경계). True 면 생략.
    reservation_guide_shown: bool
    # ── 운영-상세 발췌(평면) ──
    # operational_detail turn 에서 pre_answer prep 이 focal detail_content 를 fetch +
    # 발췌해 적재한다. answer 는 이 문자열만 소비한다(fetch·정제·발췌는 상류). 키워드
    # 부재/raw 없음/길이<게이트 → None(= attribute_gap interim 리다이렉트 폴백 신호).
    # raw 블롭은 절대 싣지 않는다(focal 단건 발췌 완료 문자열만). 리듀서 불필요.
    detail_excerpt: str | None
    # ── 카드 큐레이션(평면) ──
    # pre_answer_gate_node 가 카드형 턴에서 _curate_display 로 산출. answer 는 슬라이스·
    # extra_count 계산을 하지 않고 이 슬롯을 읽어 렌더링만 한다(생성 전용 유지).
    # curated_display: 카드 단일 출처(정규화·적합도 정렬 완료, 상위 _DISPLAY_LIMIT 건).
    # curated_extra_count: curated 잔여(= max(0, len(curated) - _DISPLAY_LIMIT)).
    # curated_alt_count: display 상위 건 중 의도 제약 불만족("대안") 항목 수(>0 시 라벨).
    # 카드형이 아니거나 결과 없음/예외면 None(answer 가 현행 슬라이스 경로로 폴백). 리듀서 불필요.
    curated_display: "list[dict[str, Any]] | None"
    curated_extra_count: int | None
    curated_alt_count: int | None
    # ── 오류/캐시 (평면) ──
    error: str | None  # 오류 메시지 (있을 경우)
    cache_hit: bool  # cache_check_node 결과 (기본값 False)
    # singleflight 락 키 겸 answer 저장 타깃 — 최초 cache_check_node 가 락을 획득한
    # 패스에서 K_original(= 사용자 원 질의가 산출하는 키)로 1회 기록하고, 이후 재시도
    # 재진입에서도 보존한다(cache_check 가드가 슬롯 존재 시 재획득·덮어쓰기 skip). 락은
    # 전 요청 수명 동안 K_original 에 유지되고 cache_write 가 이 키로 저장·해제한다.
    # self-correction 완화(K_relaxed)가 있어도 저장 키를 K_original 로 고정해, 동일 원
    # 질의 재요청이 hit 하고 K_original 을 폴링하던 singleflight 대기자도 hit 한다.
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
