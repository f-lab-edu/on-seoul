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
from schemas.state import IntentType
from tests.helpers import make_agent_state


def _make_agent(params: _AnalyticsParams) -> AnalyticsAgent:
    """_chain.ainvoke 가 주어진 params 를 반환하는 AnalyticsAgent mock."""
    agent = AnalyticsAgent.__new__(AnalyticsAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=params)
    agent._chain = chain
    return agent


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

        assert result["analytics_group_by"] == "max_class_name"
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

        assert result["analytics_group_by"] == "min_class_name"
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

        assert result["analytics_results"] == rows
        assert result["analytics_group_by"] == "area_name"
        assert result["analytics_metric"] == "count"
        # LLM 추출 키워드는 trace 관측을 위해 state 에 보존돼야 한다 (MAJOR 1).
        assert result["analytics_keyword"] == "테니스장"
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

        assert result["analytics_group_by"] in _DIMENSION_COLUMNS
        assert mock_search.await_args.kwargs["group_by"] in _DIMENSION_COLUMNS


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
