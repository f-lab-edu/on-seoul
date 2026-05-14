from enum import Enum
from typing import Any, TypedDict


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
    lat: float | None  # 사용자 위치 위도 (latitude)
    lng: float | None  # 사용자 위치 경도 (longitude)
    refined_query: str | None  # Vector Agent가 정제한 질의
    sql_results: list[dict[str, Any]] | None  # SQL Agent 결과
    vector_results: list[dict[str, Any]] | None  # Vector Agent 결과
    map_results: dict[str, Any] | None  # map_search GeoJSON FeatureCollection 결과
    answer: str | None  # Answer Agent가 생성한 최종 답변
    title: str | None  # Answer Agent가 생성한 대화 제목 (title_needed=True일 때)
    trace: dict[str, Any] | None  # LangGraph 실행 메타데이터
    error: str | None  # 오류 메시지 (있을 경우)
    # LangGraph 자기 교정(Self-Correction) 루프 카운터.
    # answer가 비어 있거나 error가 있을 때 최대 1회 재검색을 허용한다.
    retry_count: int  # 재시도 횟수 (0 = 아직 재시도 없음)
