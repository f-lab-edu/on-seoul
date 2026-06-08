"""TriageAgent 단위 테스트.

실제 LLM 호출 없이 Mock LLM으로 action/intent 분류 동작을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock


from agents.triage_agent import TriageAgent, TriageOutput
from schemas.state import ActionType, IntentType


def _make_triage(
    action: ActionType,
    primary_intent: IntentType | None = None,
    secondary_intent: IntentType | None = None,
    out_of_scope_type: str | None = None,
    refined_query: str | None = None,
    user_rationale: str | None = None,
    vector_sub_intent: str | None = None,
) -> TriageAgent:
    """지정된 output을 반환하는 Mock LLM이 주입된 TriageAgent."""
    agent = TriageAgent.__new__(TriageAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(
        return_value=TriageOutput(
            action=action,
            primary_intent=primary_intent,
            secondary_intent=secondary_intent,
            intent=primary_intent if action == ActionType.RETRIEVE and primary_intent else IntentType.FALLBACK,
            out_of_scope_type=out_of_scope_type,
            refined_query=refined_query,
            user_rationale=user_rationale,
            vector_sub_intent=vector_sub_intent,
        )
    )
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    agent._build_context_block = lambda history: ""
    return agent


class TestTriageAgentActionClassification:
    async def test_retrieve_sql_action(self):
        """RETRIEVE/SQL_SEARCH action이 반환된다."""
        agent = _make_triage(ActionType.RETRIEVE, primary_intent=IntentType.SQL_SEARCH)
        result = await agent.classify("마포구 접수중인 문화행사")
        assert result.action == ActionType.RETRIEVE
        assert result.primary_intent == IntentType.SQL_SEARCH
        assert result.intent == IntentType.SQL_SEARCH

    async def test_retrieve_vector_action(self):
        """RETRIEVE/VECTOR_SEARCH action이 반환된다."""
        agent = _make_triage(ActionType.RETRIEVE, primary_intent=IntentType.VECTOR_SEARCH)
        result = await agent.classify("아이랑 즐길 수 있는 체험")
        assert result.action == ActionType.RETRIEVE
        assert result.primary_intent == IntentType.VECTOR_SEARCH

    async def test_retrieve_map_action(self):
        """RETRIEVE/MAP action이 반환된다."""
        agent = _make_triage(ActionType.RETRIEVE, primary_intent=IntentType.MAP)
        result = await agent.classify("내 주변 체육관 지도로")
        assert result.action == ActionType.RETRIEVE
        assert result.primary_intent == IntentType.MAP

    async def test_retrieve_analytics_action(self):
        """RETRIEVE/ANALYTICS action이 반환된다."""
        agent = _make_triage(ActionType.RETRIEVE, primary_intent=IntentType.ANALYTICS)
        result = await agent.classify("테니스장 자치구별 몇 개")
        assert result.action == ActionType.RETRIEVE
        assert result.primary_intent == IntentType.ANALYTICS

    async def test_direct_answer_action(self):
        """DIRECT_ANSWER action이 반환된다."""
        agent = _make_triage(ActionType.DIRECT_ANSWER)
        result = await agent.classify("안녕하세요")
        assert result.action == ActionType.DIRECT_ANSWER
        assert result.primary_intent is None
        assert result.intent == IntentType.FALLBACK

    async def test_ambiguous_action(self):
        """AMBIGUOUS action이 반환된다."""
        agent = _make_triage(ActionType.AMBIGUOUS)
        result = await agent.classify("좋은 곳 알려줘")
        assert result.action == ActionType.AMBIGUOUS
        assert result.primary_intent is None

    async def test_out_of_scope_domain_outside(self):
        """OUT_OF_SCOPE/domain_outside action이 반환된다."""
        agent = _make_triage(ActionType.OUT_OF_SCOPE, out_of_scope_type="domain_outside")
        result = await agent.classify("오늘 서울 날씨")
        assert result.action == ActionType.OUT_OF_SCOPE
        assert result.out_of_scope_type == "domain_outside"

    async def test_out_of_scope_attribute_gap(self):
        """OUT_OF_SCOPE/attribute_gap action이 반환된다."""
        agent = _make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            refined_query="마루공원 테니스장",
            vector_sub_intent="identification",
        )
        result = await agent.classify("마루공원 테니스장 보수 공사 일정")
        assert result.action == ActionType.OUT_OF_SCOPE
        assert result.out_of_scope_type == "attribute_gap"
        assert result.refined_query == "마루공원 테니스장"
        assert result.vector_sub_intent == "identification"

    async def test_explain_action(self):
        """EXPLAIN action이 반환된다."""
        agent = _make_triage(ActionType.EXPLAIN)
        result = await agent.classify("왜 그렇게 판단했어?")
        assert result.action == ActionType.EXPLAIN

    async def test_secondary_intent_sql_vector(self):
        """SQL+VECTOR 경계 모호 시 secondary_intent가 채워진다."""
        agent = _make_triage(
            ActionType.RETRIEVE,
            primary_intent=IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        result = await agent.classify("마포구 풋살장")
        assert result.secondary_intent == IntentType.VECTOR_SEARCH

    async def test_intent_synced_to_primary_on_retrieve(self):
        """action=RETRIEVE일 때 intent가 primary_intent와 동기화된다."""
        agent = _make_triage(ActionType.RETRIEVE, primary_intent=IntentType.SQL_SEARCH)
        result = await agent.classify("마포구 수영장")
        assert result.intent == IntentType.SQL_SEARCH

    async def test_intent_fallback_on_non_retrieve(self):
        """비-RETRIEVE action이면 intent=FALLBACK이다."""
        agent = _make_triage(ActionType.DIRECT_ANSWER)
        result = await agent.classify("안녕")
        assert result.intent == IntentType.FALLBACK

    async def test_user_rationale_included(self):
        """user_rationale 필드가 반환된다."""
        agent = _make_triage(
            ActionType.RETRIEVE,
            primary_intent=IntentType.SQL_SEARCH,
            user_rationale="마포구 문화행사를 검색합니다.",
        )
        result = await agent.classify("마포구 문화행사")
        assert result.user_rationale == "마포구 문화행사를 검색합니다."

    async def test_secondary_intent_validation(self):
        """secondary_intent는 SQL_SEARCH 또는 VECTOR_SEARCH만 허용된다."""
        # MAP은 secondary_intent로 허용되지 않으므로 None으로 정규화
        output = TriageOutput(
            action=ActionType.RETRIEVE,
            primary_intent=IntentType.SQL_SEARCH,
            secondary_intent="MAP",  # type: ignore[arg-type]
            intent=IntentType.SQL_SEARCH,
        )
        assert output.secondary_intent is None


class TestTriageOutputModelPostInit:
    def test_retrieve_sets_intent_to_primary(self):
        """action=RETRIEVE 시 intent = primary_intent."""
        output = TriageOutput(
            action=ActionType.RETRIEVE,
            primary_intent=IntentType.VECTOR_SEARCH,
            intent=IntentType.FALLBACK,  # 초기값 — post_init에서 덮어쓰여야 함
        )
        assert output.intent == IntentType.VECTOR_SEARCH

    def test_non_retrieve_sets_intent_to_fallback(self):
        """비-RETRIEVE action이면 intent = FALLBACK."""
        for action in (
            ActionType.DIRECT_ANSWER,
            ActionType.AMBIGUOUS,
            ActionType.OUT_OF_SCOPE,
            ActionType.EXPLAIN,
        ):
            output = TriageOutput(
                action=action,
                primary_intent=IntentType.SQL_SEARCH,
                intent=IntentType.SQL_SEARCH,  # 초기값 — FALLBACK으로 덮어쓰여야 함
            )
            assert output.intent == IntentType.FALLBACK, (
                f"action={action}일 때 intent가 FALLBACK이어야 함"
            )

    def test_retrieve_without_primary_uses_fallback(self):
        """action=RETRIEVE이지만 primary_intent=None이면 intent=FALLBACK."""
        output = TriageOutput(
            action=ActionType.RETRIEVE,
            primary_intent=None,
            intent=IntentType.SQL_SEARCH,
        )
        assert output.intent == IntentType.FALLBACK
