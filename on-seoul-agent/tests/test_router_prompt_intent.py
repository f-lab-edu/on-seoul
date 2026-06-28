"""Router 프롬프트 의도분류 보정 가드 테스트 (케이스 1a/1b).

가짜 LLM(분류 동작)과 프롬프트 문자열 수준 가드를 함께 검증한다.
실제 LLM은 호출하지 않는다.

- 1a: 지명/장소 "알아?/있어?" 프레이밍 → VECTOR_SEARCH (FALLBACK 금지).
      회귀: 순수 잡담("고마워", "오늘 날씨 어때?") → FALLBACK 유지.
- 1b: "어떤 서비스 있어?"(열거) → 목록 검색(SQL/VECTOR).
      회귀: "어떤 종류의 서비스 있어?"/"몇 개야?"/"자치구별 분포" → ANALYTICS 유지.
"""

from unittest.mock import AsyncMock, MagicMock

from agents.router_agent import RouterAgent, _IntentOutput
from llm.prompts.router import (
    ROUTER_FEW_SHOT_EXAMPLES,
    ROUTER_SYSTEM,
)
from schemas.state import IntentType


def _make_agent(out: _IntentOutput) -> RouterAgent:
    agent = RouterAgent.__new__(RouterAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=out)
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    return agent


class TestPromptGuardFallback:
    """1a — FALLBACK 정의 축소 + 지명/키워드 → 검색 규칙이 프롬프트에 있는지."""

    def test_fallback_narrowed_to_pure_chitchat(self):
        """FALLBACK 정의가 '순수 잡담'으로 좁혀졌는지 문자열 확인."""
        assert "순수 잡담" in ROUTER_SYSTEM
        # 지명/시설/서비스 훅이 없을 때만 FALLBACK이라는 취지가 명시됨
        assert "훅" in ROUTER_SYSTEM

    def test_placename_hook_routes_to_search(self):
        """지명/장소/키워드가 있으면 '알아?/있어?'여도 검색으로 보낸다는 규칙."""
        assert "알아?" in ROUTER_SYSTEM or "알아" in ROUTER_SYSTEM
        # 0건은 정직하게 안내하되 FALLBACK 잡담으로 보내지 않는다는 가드
        assert "0건" in ROUTER_SYSTEM

    def test_fewshot_has_placename_aware_question(self):
        """'남산 한국숲정원 알아?' → VECTOR_SEARCH few-shot 존재."""
        msgs = [e["message"] for e in ROUTER_FEW_SHOT_EXAMPLES]
        target = next((m for m in msgs if "남산 한국숲정원 알아" in m), None)
        assert target is not None
        ex = next(e for e in ROUTER_FEW_SHOT_EXAMPLES if e["message"] == target)
        assert '"VECTOR_SEARCH"' in ex["output"]


class TestPromptGuardAnalyticsBoundary:
    """1b — 열거(목록) vs 집계(개수/종류) 경계 + 대조 few-shot."""

    def test_enumeration_vs_aggregation_boundary_present(self):
        """열거 vs 집계 판별 문구가 프롬프트에 있는지."""
        assert "열거" in ROUTER_SYSTEM
        # 종류/유형은 ANALYTICS 유지
        assert "종류" in ROUTER_SYSTEM and "유형" in ROUTER_SYSTEM

    def test_contrast_fewshot_present(self):
        """'남산에 어떤 서비스 있어?'(목록) vs '어떤 종류'(ANALYTICS) 대조 few-shot."""
        list_ex = next(
            (
                e
                for e in ROUTER_FEW_SHOT_EXAMPLES
                if "남산" in e["message"] and "어떤 서비스" in e["message"]
            ),
            None,
        )
        assert list_ex is not None
        assert '"ANALYTICS"' not in list_ex["output"]

        kind_ex = next(
            (
                e
                for e in ROUTER_FEW_SHOT_EXAMPLES
                if "어떤 종류" in e["message"]
            ),
            None,
        )
        assert kind_ex is not None
        assert '"ANALYTICS"' in kind_ex["output"]


class TestClassifyBehavior:
    """가짜 LLM이 산출한 의도가 그대로 통과하는지 (회귀 포함)."""

    async def test_placename_question_to_vector(self):
        agent = _make_agent(
            _IntentOutput(
                intent=IntentType.VECTOR_SEARCH,
                refined_query="남산 한국숲정원",
                vector_sub_intent="identification",
            )
        )
        result = await agent.classify("남산 한국숲정원 알아?")
        assert result.intent == IntentType.VECTOR_SEARCH

    async def test_chitchat_stays_fallback(self):
        for msg in ("고마워", "오늘 날씨 어때?"):
            agent = _make_agent(_IntentOutput(intent=IntentType.FALLBACK))
            result = await agent.classify(msg)
            assert result.intent == IntentType.FALLBACK

    async def test_placename_enumeration_to_vector_list(self):
        agent = _make_agent(
            _IntentOutput(
                intent=IntentType.VECTOR_SEARCH,
                refined_query="남산 관련 서비스",
                vector_sub_intent="identification",
            )
        )
        result = await agent.classify("남산에 어떤 서비스 있어?")
        assert result.intent in (IntentType.VECTOR_SEARCH, IntentType.SQL_SEARCH)

    async def test_kind_question_stays_analytics(self):
        for msg in (
            "체육시설에는 어떤 종류의 서비스 있어?",
            "접수중인 서비스 몇 개야?",
            "테니스장 자치구별 분포 알려줘",
        ):
            agent = _make_agent(_IntentOutput(intent=IntentType.ANALYTICS))
            result = await agent.classify(msg)
            assert result.intent == IntentType.ANALYTICS
