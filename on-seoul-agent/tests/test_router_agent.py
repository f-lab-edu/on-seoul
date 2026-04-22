"""RouterAgent 단위 테스트.

실제 LLM 호출 없이 Mock 체인으로 의도 분류 동작을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.router_agent import RouterAgent
from schemas.state import AgentState, IntentType


def _make_state(message: str = "테스트 메시지") -> AgentState:
    return AgentState(
        room_id=1,
        message_id=1,
        message=message,
        title_needed=False,
        intent=None,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )


def _make_agent(intent: IntentType) -> RouterAgent:
    """지정된 intent를 반환하는 Mock 체인이 주입된 RouterAgent."""
    from agents.router_agent import _IntentOutput

    agent = RouterAgent.__new__(RouterAgent)
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    agent._chain = mock_chain
    return agent


class TestRouterAgent:
    async def test_classify_returns_sql_search(self):
        """SQL_SEARCH 의도가 state에 반영된다."""
        agent = _make_agent(IntentType.SQL_SEARCH)
        state = _make_state("지금 접수 중인 수영장 알려줘")

        result = await agent.classify(state)

        assert result["intent"] == IntentType.SQL_SEARCH

    async def test_classify_returns_vector_search(self):
        """VECTOR_SEARCH 의도가 state에 반영된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)
        result = await agent.classify(_make_state("아이랑 체험할 수 있는 시설 추천"))

        assert result["intent"] == IntentType.VECTOR_SEARCH

    async def test_classify_returns_map(self):
        """MAP 의도가 state에 반영된다."""
        agent = _make_agent(IntentType.MAP)
        result = await agent.classify(_make_state("내 주변 체육관 지도로 보여줘"))

        assert result["intent"] == IntentType.MAP

    async def test_classify_returns_fallback(self):
        """FALLBACK 의도가 state에 반영된다."""
        agent = _make_agent(IntentType.FALLBACK)
        result = await agent.classify(_make_state("안녕하세요"))

        assert result["intent"] == IntentType.FALLBACK

    async def test_classify_preserves_other_state_fields(self):
        """classify는 intent만 변경하고 나머지 state 필드를 보존한다."""
        agent = _make_agent(IntentType.SQL_SEARCH)
        state = _make_state("수영장")
        state["room_id"] = 42
        state["message_id"] = 99

        result = await agent.classify(state)

        assert result["room_id"] == 42
        assert result["message_id"] == 99
        assert result["message"] == "수영장"

    async def test_chain_receives_message(self):
        """classify가 chain.ainvoke에 message를 전달한다."""
        agent = _make_agent(IntentType.SQL_SEARCH)
        state = _make_state("마포구 문화행사")

        await agent.classify(state)

        agent._chain.ainvoke.assert_called_once_with({"message": "마포구 문화행사"})
