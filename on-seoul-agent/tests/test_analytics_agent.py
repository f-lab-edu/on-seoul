"""AnalyticsAgent 단위 테스트 (ANALYTICS intent Phase B).

검증 대상:
- _AnalyticsParams 정규화 (허용 외 group_by/metric → 안전 기본값)
- 정합성 가드 (group_by=min_class_name 인데 max_class_name 필터 없음 → max_class_name 폴백)
- router 산출 필터(max_class_name/area_name/service_status) 재사용
- analytics_search 호출 인자 검증
- 확정된 group_by/metric 을 state 로 반환

LLM·DB 는 호출하지 않고 _chain.ainvoke / analytics_search 를 mock 한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.analytics_agent import AnalyticsAgent, _AnalyticsParams
from llm.prompts.analytics_extraction import (
    ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES,
)
from schemas.state import IntentType
from tests.helpers import make_agent_state


def _make_agent(params: _AnalyticsParams) -> AnalyticsAgent:
    """_chain.ainvoke 가 주어진 params 를 반환하는 AnalyticsAgent mock."""
    agent = AnalyticsAgent.__new__(AnalyticsAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=params)
    agent._chain = chain
    return agent


def _make_agent_with_capturing_llm() -> tuple[AnalyticsAgent, dict]:
    """실제 __init__ 을 태운 AnalyticsAgent + 프롬프트 메시지를 캡처하는 mock LLM.

    chain 전체를 mock 하던 _make_agent 와 달리, 실제 프롬프트 조립(system + few-shot
    + human)을 거치게 한다. AnalyticsAgent 는 `prompt | llm.with_structured_output(...)`
    LCEL 파이프를 쓰므로, with_structured_output 가 **Runnable** 을 반환해야 파이프가
    동작한다. RunnableLambda 로 format 된 ChatPromptValue 를 가로채 메시지를 캡처한다.
    """
    from langchain_core.runnables import RunnableLambda

    captured: dict = {}

    def _capture(prompt_value):
        captured["messages"] = prompt_value.to_messages()
        return _AnalyticsParams(group_by="max_class_name")

    mock_llm = MagicMock()
    mock_llm.with_structured_output = MagicMock(return_value=RunnableLambda(_capture))
    agent = AnalyticsAgent(model=mock_llm)
    return agent, captured


# ---------------------------------------------------------------------------
# 1. _AnalyticsParams 정규화
# ---------------------------------------------------------------------------


class TestAnalyticsParamsNormalization:
    def test_invalid_group_by_falls_back_to_max_class_name(self):
        params = _AnalyticsParams(group_by="not_a_dimension")  # type: ignore[arg-type]
        assert params.group_by == "max_class_name"

    def test_invalid_metric_falls_back_to_count(self):
        params = _AnalyticsParams(group_by="area_name", metric="sum")  # type: ignore[arg-type]
        assert params.metric == "count"

    def test_valid_values_preserved(self):
        params = _AnalyticsParams(
            group_by="min_class_name", metric="distinct", keyword="도서관"
        )
        assert params.group_by == "min_class_name"
        assert params.metric == "distinct"
        assert params.keyword == "도서관"

    def test_defaults(self):
        params = _AnalyticsParams(group_by="area_name")
        assert params.metric == "count"
        assert params.keyword is None


# ---------------------------------------------------------------------------
# 2. AnalyticsAgent.run — 정합성 가드 / 필터 재사용 / 호출 인자
# ---------------------------------------------------------------------------


class TestAnalyticsAgentRun:
    async def test_min_class_grouping_without_max_filter_falls_back(self):
        """group_by=min_class_name 인데 max_class_name 필터가 없으면 max_class_name 폴백."""
        agent = _make_agent(_AnalyticsParams(group_by="min_class_name"))
        state = make_agent_state(
            intent=IntentType.ANALYTICS, message="유형 알려줘", max_class_name=None
        )
        session = MagicMock()

        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=[])
        ) as mock_search:
            result = await agent.run(state, session)

        assert result["analytics"]["group_by"] == "max_class_name"
        # analytics_search 에도 폴백된 group_by 가 전달돼야 한다 (KeyError 방어).
        assert mock_search.await_args.kwargs["group_by"] == "max_class_name"

    async def test_min_class_grouping_with_max_filter_kept(self):
        """max_class_name 필터가 있으면 min_class_name 그룹핑을 유지한다."""
        agent = _make_agent(_AnalyticsParams(group_by="min_class_name"))
        state = make_agent_state(
            intent=IntentType.ANALYTICS,
            message="체육시설 유형 알려줘",
            max_class_name="체육시설",
        )
        session = MagicMock()

        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=[])
        ) as mock_search:
            result = await agent.run(state, session)

        assert result["analytics"]["group_by"] == "min_class_name"
        assert mock_search.await_args.kwargs["group_by"] == "min_class_name"
        assert mock_search.await_args.kwargs["max_class_name"] == "체육시설"

    async def test_router_filters_reused(self):
        """router 산출 area_name/service_status 가 analytics_search 로 전달된다."""
        agent = _make_agent(_AnalyticsParams(group_by="max_class_name"))
        state = make_agent_state(
            intent=IntentType.ANALYTICS,
            message="강남구 접수중 유형",
            area_name="강남구",
            service_status="접수중",
        )
        session = MagicMock()

        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=[])
        ) as mock_search:
            await agent.run(state, session)

        kwargs = mock_search.await_args.kwargs
        assert kwargs["area_name"] == "강남구"
        assert kwargs["service_status"] == "접수중"

    async def test_results_and_metric_returned(self):
        """analytics_search 결과 + 확정 group_by/metric 을 state 로 반환한다."""
        rows = [{"group_value": "강서구", "count": 7}]
        agent = _make_agent(
            _AnalyticsParams(group_by="area_name", metric="count", keyword="테니스장")
        )
        state = make_agent_state(intent=IntentType.ANALYTICS, message="테니스장 분포")
        session = MagicMock()

        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=rows)
        ) as mock_search:
            result = await agent.run(state, session)

        assert result["analytics"]["results"] == rows
        assert result["analytics"]["group_by"] == "area_name"
        assert result["analytics"]["metric"] == "count"
        # LLM 추출 키워드는 trace 관측을 위해 state 에 보존돼야 한다 (MAJOR 1).
        assert result["analytics"]["keyword"] == "테니스장"
        assert mock_search.await_args.kwargs["keyword"] == "테니스장"

    async def test_group_by_always_in_whitelist(self):
        """반환되는 group_by 는 항상 _DIMENSION_COLUMNS 화이트리스트 안에 있다."""
        from tools.analytics_search import _DIMENSION_COLUMNS

        agent = _make_agent(_AnalyticsParams(group_by="not_real"))  # type: ignore[arg-type]
        state = make_agent_state(intent=IntentType.ANALYTICS, message="개수")
        session = MagicMock()

        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=[])
        ) as mock_search:
            result = await agent.run(state, session)

        assert result["analytics"]["group_by"] in _DIMENSION_COLUMNS
        assert mock_search.await_args.kwargs["group_by"] in _DIMENSION_COLUMNS


# ---------------------------------------------------------------------------
# 3. 프롬프트 조립 스모크 테스트 — 실제 __init__ 이 system + few-shot 을
#    올바르게 조립하는지 검증 (chain 전체 mock 을 우회).
# ---------------------------------------------------------------------------


class TestAnalyticsPromptAssembly:
    async def _capture_messages(self, message: str):
        """실제 __init__ 으로 조립한 체인을 ainvoke 하고 LLM 에 전달된 메시지를 반환."""
        agent, captured = _make_agent_with_capturing_llm()
        state = make_agent_state(intent=IntentType.ANALYTICS, message=message)
        session = MagicMock()
        with patch(
            "agents.analytics_agent.analytics_search", AsyncMock(return_value=[])
        ):
            await agent.run(state, session)
        return captured["messages"]

    async def test_system_message_is_first(self):
        """첫 메시지는 system 프롬프트여야 한다."""
        messages = await self._capture_messages("강남구 테니스장 분포")
        assert messages[0].type == "system"

    async def test_few_shot_pairs_injected_after_system(self):
        """few-shot 예시가 system 다음에 human/ai 쌍으로 주입된다.

        구조: [SystemMessage, *few_shot(HumanMsg+AIMsg 쌍), HumanMessage(actual)].
        few-shot 예시를 프롬프트에서 누락하면 길이 불일치로 실패한다 (mutation 가드).
        """
        messages = await self._capture_messages("강남구 테니스장 분포")
        # group_by 4종 판단을 시연하려면 최소 4개 예시가 필요하다 (회귀 가드: 예시를
        # 통째로 삭제/축소하면 실패).
        assert len(ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES) >= 4
        expected_len = 1 + len(ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES) * 2 + 1
        assert len(messages) == expected_len
        # few-shot 영역은 human/ai 가 번갈아 나타난다.
        few_shot_msgs = messages[1:-1]
        assert [m.type for m in few_shot_msgs] == [
            "human" if i % 2 == 0 else "ai" for i in range(len(few_shot_msgs))
        ]

    async def test_actual_user_message_is_last(self):
        """마지막 메시지는 실제 사용자 발화여야 한다."""
        messages = await self._capture_messages("강남구 테니스장 분포")
        assert messages[-1].type == "human"
        assert "강남구 테니스장 분포" in messages[-1].content

    async def test_system_prompt_contains_core_dimension_rules(self):
        """system 프롬프트에 group_by 화이트리스트·판단 규칙 핵심 문구가 포함된다.

        프롬프트에서 차원 키워드/규칙을 삭제하면 실패한다 (mutation 가드).
        """
        messages = await self._capture_messages("개수")
        system = messages[0].content
        # group_by 화이트리스트 4종
        for keyword in (
            "area_name",
            "max_class_name",
            "min_class_name",
            "service_status",
        ):
            assert keyword in system, f"system prompt 에 {keyword} 누락"
        # metric 2종
        assert "count" in system
        assert "distinct" in system
        # 대분류 명시 → min_class_name 분기 규칙
        assert "대분류" in system
        assert "min_class_name" in system

    async def test_few_shot_examples_render_messages_and_outputs(self):
        """few-shot 예시의 message/output 텍스트가 실제 메시지에 렌더된다."""
        messages = await self._capture_messages("개수")
        rendered = "\n".join(m.content for m in messages[1:-1])
        for ex in ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES:
            assert ex["message"] in rendered
            assert ex["output"] in rendered


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
