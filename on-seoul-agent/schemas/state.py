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


class AgentState(TypedDict):
    room_id: int
    message_id: int
    message: str  # 사용자 원본 질문
    title_needed: bool  # 제목 생성 필요 여부
    intent: IntentType | None  # SQL_SEARCH / VECTOR_SEARCH / MAP / ANALYTICS / FALLBACK
    # 방향성 재시도: retry_prep_node 가 다음 순회의 intent 를 강제할 때 세팅.
    # router_node 가 존재 시 LLM 분류를 skip 하고 이 값을 사용한다(1회성, 즉시 소비).
    # None 이면 일반 분류(기존 동작).
    forced_intent: IntentType | None
    # MAP 0건 재시도 시 확장 반경(m). map_node 가 존재 시 기본 반경 대신 사용한다.
    # None 이면 _MAP_DEFAULT_RADIUS_M(1000) 적용(기존 동작).
    retry_radius_m: int | None
    # MAP intent 반경 검색용 좌표 (ChatRequest.lat/lng 로부터 주입).
    # None이면 MAP intent를 FALLBACK으로 대체한다.
    user_lat: float | None  # 클라이언트 위도 (latitude)
    user_lng: float | None  # 클라이언트 경도 (longitude)
    refined_query: str | None  # Router(우선) 또는 Vector Agent(fallback)가 정제한 질의
    # Router가 함께 산출하는 post-filter 메타데이터.
    # SQL_SEARCH / VECTOR_SEARCH 경로의 검색 도구에 전달되어 결과를 좁힌다.
    # 추출 불가 시 None. 허용 값은 router_agent._IntentOutput 검증을 따른다.
    max_class_name: str | None  # 체육시설·문화행사·시설대관·교육·진료 중 하나
    area_name: str | None  # 서울 자치구명 (예: 강남구)
    service_status: str | None  # 접수중·예약마감·접수종료·예약일시중지·안내중 중 하나
    payment_type: (
        str | None
    )  # 결제 유형 필터 ("무료"/"유료"). 무료=정확, 유료=접두 매칭
    sql_results: list[dict[str, Any]] | None  # SQL Agent 결과
    sql_keyword: str | None  # SqlAgent가 LLM으로 추출한 키워드 (search_channels 적재용)
    vector_sub_intent: (
        str | None
    )  # Router가 분류한 벡터 검색 세부 의도 (VECTOR_SEARCH 전용)
    vector_results: list[dict[str, Any]] | None  # Vector Agent 결과
    map_results: dict[str, Any] | None  # map_search GeoJSON FeatureCollection 결과
    # ─── ANALYTICS (집계/분포 질의) ───
    # analytics_node 가 analytics_search 결과를 직접 채우는 슬롯 (hydration 없음).
    # 각 행은 {"group_value": ..., "count": ...} 형태 (distinct 는 count 생략/None).
    # 빈 결과: []. 미설정: None (다른 intent 경로 또는 미실행).
    analytics_results: list[dict[str, Any]] | None  # 집계 결과 행 (group_value/count)
    analytics_group_by: (
        str | None
    )  # 집계 차원 (area_name/max_class_name/min_class_name/service_status)
    analytics_metric: str | None  # 집계 metric (count / distinct)
    analytics_keyword: (
        str | None
    )  # AnalyticsAgent가 LLM으로 추출한 키워드 (trace 관측용)
    # ─── Hydration (service_id → public_service_reservations 원본) ───
    # HydrationNode 가 검색 노드(sql/vector) 직후에 채우는 통합 슬롯.
    # AnswerAgent 등 후속 단계는 이 슬롯을 사용하여 검색 경로에 의존하지 않는다.
    #
    # 책임 분리:
    #   - VECTOR_SEARCH: VectorAgent 는 vector_results 에 {service_id, rrf_score, ...} 만 채운다.
    #                    HydrationNode 가 service_id 를 추출하여 hydrate_services 호출 + 메타 머지.
    #   - SQL_SEARCH:    sql_search 가 이미 원본 행을 반환하므로 HydrationNode 는 통과.
    #
    # service_id 추출 책임: agents.hydration_node._extract_service_ids() 함수
    # (intent → vector_results/sql_results 의 service_id 키 추출).
    # 별도 슬롯을 두지 않는 이유는 State 단일 진실원 원칙을 위배하지 않기 위함.
    hydrated_services: list[dict[str, Any]] | None
    # AnswerAgent 가 _normalize 통과 후 상위 _DISPLAY_LIMIT(5)건을 카드 형태로 노출.
    # LLM 컨텍스트와 동일한 dict 리스트. 프론트 카드 UI 가 직접 사용한다.
    # 빈 결과: []. 미설정: None (AnswerAgent 미실행 / cache miss 초기 상태).
    service_cards: list[dict[str, Any]] | None
    answer: str | None  # Answer Agent가 생성한 최종 답변
    title: str | None  # Answer Agent가 생성한 대화 제목 (title_needed=True일 때)
    trace: dict[str, Any] | None  # LangGraph 실행 메타데이터
    # 노드 실행 경로 누적 (관측용). node_path_reducer 가 부분 리스트를 append 병합한다.
    # GraphNodes 인스턴스 속성에서 state 로 이동(제안 0): 싱글톤 GraphNodes 가 요청별
    # 가변 경로를 인스턴스에 들고 있으면 동시 요청 간 오염되므로 per-invoke 격리되는
    # state 로 옮긴다. trace_node 가 이 값을 trace.node_path 로 적재한다.
    node_path: Annotated[list[str], node_path_reducer]
    # 그래프 실행 시작 시각 (time.monotonic()). 경과 시간(elapsed_ms) 산출용.
    # routers/chat.py 의 초기 state 구성 시 주입되며 trace_node 가 읽는다.
    started_at: float | None
    error: str | None  # 오류 메시지 (있을 경우)
    # LangGraph 자기 교정(Self-Correction) 루프 카운터.
    # answer가 비어 있거나 error가 있을 때 최대 1회 재검색을 허용한다.
    retry_count: int  # 재시도 횟수 (0 = 아직 재시도 없음)
    # 하드 필터 0건으로 인한 완화 재시도 신호. retry_prep_node 가 0건 재시도 시 True 로 세팅.
    # AnswerAgent 가 완화 사실(예: payment_type 드롭)을 답변에 명시하는 데 사용한다.
    retry_relaxed: bool
    # Router 컨텍스트 / Answer Cache 흐름
    # API 서비스가 chat_messages에서 조립한 직전 N턴 대화 이력.
    # ChatRequest.history에서 주입. 없으면 []. Router 에이전트가 맥락으로 활용.
    history: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": str}, ...]
    cache_hit: bool  # cache_check_node 결과 (기본값 False)
    # 검색 채널 관측 (chat_search_queries / chat_search_results 적재용).
    # 각 노드가 자기 채널 키 하나를 ChannelData(kind, query, hits) 로 채운다.
    # operator.or_ reducer: 부분 dict 반환 시 LangGraph가 누적 병합한다.
    # self-correction 재시도 시 retry_prep_node 가 {} 로 명시 리셋 (UNIQUE 위반 방지).
    search_channels: Annotated[dict[str, ChannelData], search_channels_reducer]
    # ─── [C] W2: RRF fusion 슬롯 ───
    # rrf_fusion_node 가 SQL+VECTOR 병렬 팬아웃 결과를 RRF 통합한 service_id 순서 리스트.
    # HydrationNode 가 이 슬롯을 우선 참조하여 hydrate_services 를 호출한다.
    # None 이면 단일 라우트(기존 동작). enable_secondary_intent=True 시만 유효.
    rrf_merged_ids: list[str] | None
    # ─── [C] W2: TriageAgent 2축 분리 슬롯 ───
    # TriageAgent가 결정하는 행동 유형. None이면 구 router 경로(하위호환).
    # AgentGraph()는 항상 TriageAgent()를 기본 주입하므로 프로덕션에서는 항상 채워진다.
    action: ActionType | None
    # OUT_OF_SCOPE 서브타입: "domain_outside" | "attribute_gap"
    out_of_scope_type: str | None
    # 사용자 노출용 판단 근거 1문장 (TriageAgent.user_rationale).
    # SSE router_decision 이벤트에 포함(W3 연계 기반).
    user_rationale: str | None
    # secondary_intent: SQL↔VECTOR 경계 모호 시 팬아웃용. None이면 단일 라우트(기존 동작).
    secondary_intent: IntentType | None
    # ─── W1: 대화 상태·결과 엔티티 carryover + 참조 해소 ───
    # 직전 턴의 결과 엔티티(정체성). 각 항목 {"service_id": str, "label": str}.
    # ChatRequest.prev_entities 에서 주입(API 서비스가 영속 service_cards 로부터 조립).
    # 미전송 시 None([]) — reference_resolution_node 가 무조건 non-referential 처리(하위호환).
    prev_entities: list[dict[str, str]] | None
    # 직전 턴의 분류 intent. ChatRequest.prev_intent 에서 주입. 미전송 시 None.
    # (현재는 carryover 슬롯으로만 보관. EXPLAIN 소비는 [C] 이후.)
    prev_intent: IntentType | None
    # 직전 턴의 판단 근거(user_rationale). ChatRequest.prev_reasoning 에서 주입.
    # 미전송 시 None. EXPLAIN action 소비는 [C] 이후 — 이번엔 슬롯·주입만(X7).
    prev_reasoning: str | None
    # 참조 해소 결과: 현재 message 가 지시 참조일 때 바인딩된 service_id 리스트.
    # reference_resolution_node 가 referential 판정 시 채운다. None([]) 이면 비-referential
    # (기존 흐름). 다중 참조("1번이랑 3번") 시 복수 바인딩.
    target_service_ids: list[str] | None
