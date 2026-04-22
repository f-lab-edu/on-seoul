"""SqlAgent 단위 테스트.

LLM 파라미터 추출과 SQL 쿼리 로직을 Mock으로 분리하여 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.sql_agent import SqlAgent, _SqlParams
from schemas.state import AgentState, IntentType


def _make_state(message: str = "수영장 알려줘") -> AgentState:
    return AgentState(
        room_id=1,
        message_id=1,
        message=message,
        title_needed=False,
        intent=IntentType.SQL_SEARCH,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )


def _make_agent(params: _SqlParams, db_rows: list[dict]) -> tuple[SqlAgent, MagicMock]:
    """지정 파라미터와 DB 결과를 반환하는 Mock Agent와 Mock Session."""
    agent = SqlAgent.__new__(SqlAgent)
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=params)
    agent._chain = mock_chain

    # Mock DB session
    mock_result = MagicMock()
    mock_result.keys.return_value = list(db_rows[0].keys()) if db_rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in db_rows]

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    return agent, mock_session


class TestSqlAgent:
    async def test_search_populates_sql_results(self):
        """search는 DB 조회 결과를 sql_results에 채운다."""
        rows = [{"service_id": "S001", "service_name": "수영장", "area_name": "강남구"}]
        agent, session = _make_agent(_SqlParams(), rows)

        result = await agent.search(_make_state(), session)

        assert result["sql_results"] == rows

    async def test_search_preserves_state_fields(self):
        """search는 sql_results만 변경하고 나머지를 보존한다."""
        agent, session = _make_agent(_SqlParams(), [])
        state = _make_state("수영장")
        state["room_id"] = 7

        result = await agent.search(state, session)

        assert result["room_id"] == 7
        assert result["message"] == "수영장"

    async def test_chain_receives_message(self):
        """LLM 체인에 message가 전달된다."""
        agent, session = _make_agent(_SqlParams(), [])
        state = _make_state("강남구 체육시설")

        await agent.search(state, session)

        agent._chain.ainvoke.assert_called_once_with({"message": "강남구 체육시설"})

    async def test_query_builds_category_filter(self):
        """max_class_name 파라미터가 있으면 WHERE에 포함된다."""
        agent, session = _make_agent(
            _SqlParams(max_class_name="체육시설"), []
        )
        await agent.search(_make_state(), session)

        call_args = session.execute.call_args
        sql_str = str(call_args[0][0])
        bind = call_args[0][1]

        assert "max_class_name" in sql_str
        assert bind.get("max_class_name") == "체육시설"

    async def test_query_builds_area_filter(self):
        """area_name 파라미터가 있으면 WHERE에 포함된다."""
        agent, session = _make_agent(_SqlParams(area_name="마포구"), [])
        await agent.search(_make_state(), session)

        bind = session.execute.call_args[0][1]
        assert bind.get("area_name") == "마포구"

    async def test_query_builds_keyword_filter(self):
        """keyword 파라미터가 있으면 ILIKE 패턴으로 변환된다."""
        agent, session = _make_agent(_SqlParams(keyword="수영"), [])
        await agent.search(_make_state(), session)

        bind = session.execute.call_args[0][1]
        assert bind.get("keyword") == "%수영%"

    async def test_query_no_extra_filter_when_params_empty(self):
        """파라미터가 모두 None이면 deleted_at IS NULL 조건만 포함된다."""
        agent, session = _make_agent(_SqlParams(), [])
        await agent.search(_make_state(), session)

        sql_str = str(session.execute.call_args[0][0])
        bind = session.execute.call_args[0][1]

        assert "deleted_at IS NULL" in sql_str
        assert bind == {}
