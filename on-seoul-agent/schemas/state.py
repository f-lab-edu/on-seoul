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
    refined_query: str | None  # Vector Agent가 정제한 질의
    sql_results: list[dict[str, Any]] | None  # SQL Agent 결과
    vector_results: list[dict[str, Any]] | None  # Vector Agent 결과
    map_results: dict[str, Any] | None  # map_search GeoJSON 결과
    answer: str | None  # Answer Agent가 생성한 최종 답변
    title: str | None  # Answer Agent가 생성한 대화 제목 (title_needed=True일 때)
    trace: dict[str, Any] | None  # LangGraph 실행 메타데이터
    error: str | None  # 오류 메시지 (있을 경우)
