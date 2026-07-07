"""RouterAgent 단위 테스트.

실제 LLM 호출 없이 Mock LLM으로 의도 분류 동작을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

from agents.router_agent import RouterAgent, _IntentOutput
from schemas.state import IntentType


def _make_agent(intent: IntentType) -> RouterAgent:
    """지정된 intent를 반환하는 Mock LLM이 주입된 RouterAgent."""
    agent = RouterAgent.__new__(RouterAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    return agent


class TestRouterAgent:
    async def test_classify_returns_sql_search(self):
        """SQL_SEARCH 의도가 반환된다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        result = await agent.classify("지금 접수 중인 수영장 알려줘")

        assert result.intent == IntentType.SQL_SEARCH

    async def test_classify_returns_vector_search(self):
        """VECTOR_SEARCH 의도가 반환된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)
        result = await agent.classify("아이랑 체험할 수 있는 시설 추천")

        assert result.intent == IntentType.VECTOR_SEARCH

    async def test_classify_returns_map(self):
        """MAP 의도가 반환된다."""
        agent = _make_agent(IntentType.MAP)
        result = await agent.classify("내 주변 체육관 지도로 보여줘")

        assert result.intent == IntentType.MAP

    async def test_classify_returns_fallback(self):
        """FALLBACK 의도가 반환된다."""
        agent = _make_agent(IntentType.FALLBACK)
        result = await agent.classify("안녕하세요")

        assert result.intent == IntentType.FALLBACK

    async def test_chain_receives_message(self):
        """classify가 LLM에 message를 HumanMessage로 전달한다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        await agent.classify("마포구 문화행사")

        structured = agent._llm.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]
        human_texts = [m.content for m in messages if m.type == "human"]
        assert any("마포구 문화행사" in t for t in human_texts)

    async def test_history_optional(self):
        """history는 기본값 None으로 생략 가능하다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        result = await agent.classify("수영장")

        assert result.intent == IntentType.SQL_SEARCH

    async def test_few_shot_messages_injected(self):
        """classify 호출 시 few-shot 예시 메시지가 SystemMessage 다음에 주입된다."""
        from llm.prompts.router import ROUTER_FEW_SHOT_EXAMPLES

        agent = _make_agent(IntentType.SQL_SEARCH)
        await agent.classify("수영장")

        structured = agent._llm.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]

        # messages 구조: [SystemMessage, *few_shot(HumanMsg+AIMsg 쌍), HumanMessage(actual)]
        assert messages[0].type == "system"
        # few-shot 예시 수 검증 (N쌍 = N*2 메시지)
        assert len(messages) == 1 + len(ROUTER_FEW_SHOT_EXAMPLES) * 2 + 1
        # 마지막 메시지가 실제 사용자 발화
        assert messages[-1].type == "human"
        assert "수영장" in messages[-1].content

    async def test_classify_returns_analytics(self):
        """ANALYTICS 의도가 반환된다."""
        agent = _make_agent(IntentType.ANALYTICS)

        result = await agent.classify("테니스장 자치구별로 몇 개씩 있어?")

        assert result.intent == IntentType.ANALYTICS

    async def test_analytics_post_filters_extracted(self):
        """ANALYTICS intent에 max_class_name·service_status post-filter가 올바르게 담긴다."""
        agent = RouterAgent.__new__(RouterAgent)
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.ANALYTICS,
                refined_query="접수중 체육시설 카테고리별 개수",
                max_class_name="체육시설",
                service_status="접수중",
                vector_sub_intent=None,
            )
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
        agent._llm = mock_llm

        result = await agent.classify("접수 중인 체육시설 카테고리별로 몇 개야?")

        assert result.intent == IntentType.ANALYTICS
        # 단일 문자열 입력도 validator 가 리스트로 정규화한다(닫힌 5종).
        assert result.max_class_name == ["체육시설"]
        assert result.service_status == "접수중"
        assert result.vector_sub_intent is None


class TestRouterSecondaryIntent:
    """secondary_intent 추출·검증 (TriageOutput에서 _IntentOutput으로 이관)."""

    async def test_secondary_intent_sql_vector(self):
        """SQL↔VECTOR 경계 모호 시 secondary_intent가 채워진다."""
        agent = RouterAgent.__new__(RouterAgent)
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.SQL_SEARCH,
                refined_query="마포구 풋살장",
                secondary_intent=IntentType.VECTOR_SEARCH,
            )
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
        agent._llm = mock_llm

        result = await agent.classify("마포구 풋살장")
        assert result.intent == IntentType.SQL_SEARCH
        assert result.secondary_intent == IntentType.VECTOR_SEARCH

    def test_secondary_intent_map_normalized_to_none(self):
        """secondary_intent에 MAP은 허용되지 않아 None이 된다."""
        out = _IntentOutput(
            intent=IntentType.SQL_SEARCH,
            secondary_intent="MAP",  # type: ignore[arg-type]
        )
        assert out.secondary_intent is None

    def test_secondary_intent_analytics_normalized_to_none(self):
        """secondary_intent에 ANALYTICS는 허용되지 않아 None이 된다."""
        out = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            secondary_intent="ANALYTICS",  # type: ignore[arg-type]
        )
        assert out.secondary_intent is None

    def test_secondary_intent_vector_as_inttype(self):
        """secondary_intent에 IntentType.VECTOR_SEARCH는 허용된다."""
        out = _IntentOutput(
            intent=IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        assert out.secondary_intent == IntentType.VECTOR_SEARCH

    def test_secondary_intent_default_none(self):
        """secondary_intent 기본값은 None."""
        out = _IntentOutput(intent=IntentType.SQL_SEARCH)
        assert out.secondary_intent is None
