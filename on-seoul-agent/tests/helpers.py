"""테스트 공통 헬퍼 — AgentState 팩토리.

AgentState에 필드가 추가될 때 make_agent_state만 수정하면 된다.
각 테스트 파일은 이 함수를 호출하는 얇은 래퍼로 파일별 기본값만 선언한다.

사용법::

    from tests.helpers import make_agent_state
    state = make_agent_state(intent=IntentType.SQL_SEARCH, message="테스트")
"""

from typing import Any

from schemas.state import AgentState


def make_agent_state(**overrides: Any) -> AgentState:
    """AgentState 테스트 팩토리 — 최소 유효 상태를 기본값으로 반환한다."""
    base = AgentState(
        room_id=1,
        message_id=1,
        message="수영장 알려줘",
        title_needed=False,
        intent=None,
        lat=None,
        lng=None,
        refined_query=None,
        max_class_name=None,
        area_name=None,
        service_status=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
        retry_count=0,
        recent_queries=[],
        cache_hit=False,
    )
    base.update(overrides)
    return base
