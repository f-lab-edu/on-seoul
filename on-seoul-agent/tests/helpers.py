"""테스트 공통 헬퍼 — AgentState 팩토리 및 그래프 mock 빌더.

AgentState에 필드가 추가될 때 make_agent_state만 수정하면 된다.
각 테스트 파일은 이 함수를 호출하는 얇은 래퍼로 파일별 기본값만 선언한다.

사용법::

    from tests.helpers import make_agent_state, make_router, make_sql_agent
    state = make_agent_state(intent=IntentType.SQL_SEARCH, message="테스트")
    router = make_router(IntentType.SQL_SEARCH)
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from schemas.state import AgentState, IntentType


def make_agent_state(**overrides: Any) -> AgentState:
    """AgentState 테스트 팩토리 — 최소 유효 상태를 기본값으로 반환한다."""
    base = AgentState(
        room_id=1,
        message_id=1,
        message="수영장 알려줘",
        title_needed=False,
        intent=None,
        user_lat=None,
        user_lng=None,
        refined_query=None,
        max_class_name=None,
        area_name=None,
        service_status=None,
        sql_results=None,
        sql_keyword=None,
        vector_sub_intent=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
        retry_count=0,
        recent_queries=[],
        cache_hit=False,
        search_channels={},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 그래프 단위 테스트용 mock 빌더 (test_graph.py, test_graph_search_persist.py 공용)
# ---------------------------------------------------------------------------


def make_router(intent: IntentType) -> RouterAgent:
    """주어진 intent 를 항상 반환하는 RouterAgent mock."""
    agent = RouterAgent.__new__(RouterAgent)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    return agent


def make_sql_agent(
    rows: list[dict],
    keyword: str | None = None,
) -> tuple[SqlAgent, MagicMock]:
    """rows 를 반환하는 SqlAgent mock + data_session mock."""
    agent = SqlAgent.__new__(SqlAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_SqlParams(keyword=keyword))
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def make_answer_agent(
    answer: str = "답변입니다.",
    title: str | None = None,
) -> AnswerAgent:
    """고정 answer/title 을 반환하는 AnswerAgent mock."""
    agent = AnswerAgent.__new__(AnswerAgent)

    answer_chain = MagicMock()
    answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=answer))
    agent._answer_chain = answer_chain

    title_chain = MagicMock()
    title_chain.ainvoke = AsyncMock(
        return_value=_TitleOutput(title=title or "수영장 안내")
    )
    agent._title_chain = title_chain
    return agent


def make_ai_session() -> MagicMock:
    """on_ai DB 세션 mock — execute/commit/rollback/begin_nested 지원."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    # begin_nested()는 async context manager 로 사용된다.
    # MagicMock은 __aenter__/__aexit__ 를 AsyncMock 으로 자동 설정하므로
    # 별도 설정 없이 `async with session.begin_nested():` 가 동작한다.
    return session
