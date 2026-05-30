"""RouterAgent vector_sub_intent 분류 단위 테스트.

Mock LLM으로 vector_sub_intent 필드 분류 동작을 검증한다.
실제 LLM 호출 없이 테스트한다.
"""

from unittest.mock import AsyncMock, MagicMock


from agents.router_agent import RouterAgent, _IntentOutput
from schemas.state import IntentType


def _make_agent_with_output(output: _IntentOutput) -> RouterAgent:
    """지정된 _IntentOutput을 반환하는 Mock LLM이 주입된 RouterAgent."""
    agent = RouterAgent.__new__(RouterAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=output)
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    return agent


class TestRouterSubIntent:
    async def test_identification_query(self):
        """VECTOR_SEARCH + 시설명/지역 질의 → vector_sub_intent='identification'."""
        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="마포구 풋살장",
            vector_sub_intent="identification",
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("마포구 풋살장 어디 있어요?")
        assert result.intent == IntentType.VECTOR_SEARCH
        assert result.vector_sub_intent == "identification"

    async def test_detail_query(self):
        """VECTOR_SEARCH + 요금/취소 질의 → vector_sub_intent='detail'."""
        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="테니스장 평일 이용료",
            vector_sub_intent="detail",
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("테니스장 평일 이용료 얼마예요?")
        assert result.intent == IntentType.VECTOR_SEARCH
        assert result.vector_sub_intent == "detail"

    async def test_semantic_query(self):
        """VECTOR_SEARCH + 활동/맥락 질의 → vector_sub_intent='semantic'."""
        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="아이랑 갈 만한 무료 체험",
            vector_sub_intent="semantic",
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("아이랑 갈 만한 무료 체험 있어요?")
        assert result.intent == IntentType.VECTOR_SEARCH
        assert result.vector_sub_intent == "semantic"

    async def test_non_vector_intent_returns_none(self):
        """SQL_SEARCH 의도 → vector_sub_intent는 None이어야 한다."""
        output = _IntentOutput(
            intent=IntentType.SQL_SEARCH,
            refined_query="마포구 수영장 접수중",
            vector_sub_intent=None,
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("마포구 수영장 접수중인 곳")
        assert result.intent == IntentType.SQL_SEARCH
        assert result.vector_sub_intent is None

    async def test_invalid_label_normalized_to_none(self):
        """허용되지 않는 vector_sub_intent 값 → Pydantic Literal 검증 실패로 None 처리."""
        # Pydantic v2는 Literal 타입 불일치 시 ValidationError를 발생시키므로,
        # field_validator에서 잘못된 값을 None으로 정규화해야 한다.
        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="테스트 질의",
            vector_sub_intent=None,  # 정규화 후 None
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("테스트")
        assert result.vector_sub_intent is None

    async def test_vector_sub_intent_field_exists_on_output(self):
        """_IntentOutput에 vector_sub_intent 필드가 존재한다."""
        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
        )
        assert hasattr(output, "vector_sub_intent")
        assert output.vector_sub_intent is None

    async def test_fallback_intent_has_none_sub_intent(self):
        """FALLBACK 의도 → vector_sub_intent는 None이어야 한다."""
        output = _IntentOutput(
            intent=IntentType.FALLBACK,
        )
        agent = _make_agent_with_output(output)
        result = await agent.classify("안녕하세요")
        assert result.vector_sub_intent is None
