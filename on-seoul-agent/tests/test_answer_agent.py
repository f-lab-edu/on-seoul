"""AnswerAgent 단위 테스트.

답변 생성, 시설 카드 정규화, 제목 생성, fallback URL 처리를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from schemas.state import AgentState, IntentType


def _make_state(**kwargs) -> AgentState:
    base = AgentState(
        room_id=1,
        message_id=1,
        message="수영장 알려줘",
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
    base.update(kwargs)
    return base


def _make_agent(
    answer_text: str = "수영장 목록입니다.",
    title_text: str | None = None,
) -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)

    mock_answer_chain = MagicMock()
    mock_answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=answer_text))
    agent._answer_chain = mock_answer_chain

    mock_title_chain = MagicMock()
    mock_title_chain.ainvoke = AsyncMock(
        return_value=_TitleOutput(title=title_text or "수영장 조회")
    )
    agent._title_chain = mock_title_chain

    return agent


class TestAnswerAgent:
    async def test_answer_populates_answer_field(self):
        """answer 메서드는 생성된 답변을 state.answer에 채운다."""
        agent = _make_agent("강남구 수영장은 현재 접수 중입니다.")
        result = await agent.answer(_make_state())

        assert result["answer"] == "강남구 수영장은 현재 접수 중입니다."

    async def test_title_not_generated_when_not_needed(self):
        """title_needed=False면 title_chain이 호출되지 않고 title은 None이다."""
        agent = _make_agent()
        result = await agent.answer(_make_state(title_needed=False))

        agent._title_chain.ainvoke.assert_not_called()
        assert result.get("title") is None

    async def test_title_generated_when_needed(self):
        """title_needed=True면 title_chain이 호출되고 title이 채워진다."""
        agent = _make_agent(title_text="수영장 안내")
        result = await agent.answer(_make_state(title_needed=True))

        agent._title_chain.ainvoke.assert_called_once()
        assert result["title"] == "수영장 안내"

    async def test_answer_chain_receives_message_and_results(self):
        """answer_chain에 message와 results_json이 전달된다."""
        agent = _make_agent()
        rows = [{"service_name": "수영장", "service_url": "https://example.com"}]
        state = _make_state(message="수영장", sql_results=rows)

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert call_kwargs["message"] == "수영장"
        assert "수영장" in call_kwargs["results_json"]

    async def test_collect_results_merges_sql_and_vector(self):
        """sql_results와 vector_results가 모두 있으면 합쳐서 전달된다."""
        agent = _make_agent()
        sql_rows = [{"service_id": "S001", "service_name": "수영장", "service_url": None}]
        vec_rows = [{"service_id": "S002", "service_name": "체험관", "service_url": None}]
        state = _make_state(sql_results=sql_rows, vector_results=vec_rows)

        await agent.answer(state)

        results_json = agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        assert "수영장" in results_json
        assert "체험관" in results_json

    async def test_normalize_uses_fallback_url_when_missing(self):
        """service_url이 없으면 fallback URL로 대체된다."""
        from agents.answer_agent import _FALLBACK_URL, AnswerAgent

        row = {"service_id": "S001", "service_name": "수영장", "service_url": None}
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == _FALLBACK_URL

    async def test_normalize_keeps_existing_url(self):
        """service_url이 있으면 그대로 유지된다."""
        from agents.answer_agent import AnswerAgent

        row = {
            "service_id": "S001",
            "service_name": "수영장",
            "service_url": "https://yeyak.seoul.go.kr/svc/001",
        }
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == "https://yeyak.seoul.go.kr/svc/001"

    async def test_answer_preserves_state_fields(self):
        """answer는 answer/title 외 나머지 state를 보존한다."""
        agent = _make_agent()
        state = _make_state(room_id=42, message_id=7)

        result = await agent.answer(state)

        assert result["room_id"] == 42
        assert result["message_id"] == 7
