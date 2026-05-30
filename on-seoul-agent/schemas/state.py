from enum import Enum
from typing import Annotated, Any, TypedDict

from schemas.search import ChannelData, search_channels_reducer


class IntentType(str, Enum):
    SQL_SEARCH = "SQL_SEARCH"
    VECTOR_SEARCH = "VECTOR_SEARCH"
    MAP = "MAP"
    FALLBACK = "FALLBACK"


class AgentState(TypedDict):
    room_id: int
    message_id: int
    message: str  # 사용자 원본 질문
    title_needed: bool  # 제목 생성 필요 여부
    intent: IntentType | None  # SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK
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
    sql_results: list[dict[str, Any]] | None  # SQL Agent 결과
    sql_keyword: str | None  # SqlAgent가 LLM으로 추출한 키워드 (search_channels 적재용)
    vector_sub_intent: (
        str | None
    )  # Router가 분류한 벡터 검색 세부 의도 (VECTOR_SEARCH 전용)
    vector_results: list[dict[str, Any]] | None  # Vector Agent 결과
    map_results: dict[str, Any] | None  # map_search GeoJSON FeatureCollection 결과
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
    error: str | None  # 오류 메시지 (있을 경우)
    # LangGraph 자기 교정(Self-Correction) 루프 카운터.
    # answer가 비어 있거나 error가 있을 때 최대 1회 재검색을 허용한다.
    retry_count: int  # 재시도 횟수 (0 = 아직 재시도 없음)
    # Router 컨텍스트 / Answer Cache 흐름
    recent_queries: list[str]  # router에 주입할 follow-up 컨텍스트 (기본값 [])
    cache_hit: bool  # cache_check_node 결과 (기본값 False)
    # 검색 채널 관측 (chat_search_queries / chat_search_results 적재용).
    # 각 노드가 자기 채널 키 하나를 ChannelData(kind, query, hits) 로 채운다.
    # operator.or_ reducer: 부분 dict 반환 시 LangGraph가 누적 병합한다.
    # self-correction 재시도 시 retry_prep_node 가 {} 로 명시 리셋 (UNIQUE 위반 방지).
    search_channels: Annotated[dict[str, ChannelData], search_channels_reducer]
