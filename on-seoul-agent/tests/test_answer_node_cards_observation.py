"""answer_node C5 관측 회귀 봉인.

검색 결과(hydrated_services / sql_results)는 존재하는데 service_cards 가 비어 있는
경우 logger.warning 으로 normalize 무음 실패를 관측한다. 동작 변경은 없다.

로깅 설정(setup_logging 의 propagate=False)이 실행 순서에 따라 caplog 캡처를
방해하므로, logger.warning 호출을 직접 mock 하여 결정적으로 단언한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.nodes import GraphNodes
from schemas.state import AgentState, IntentType
from tests.helpers import make_agent_state


def _make_nodes(returned_state: AgentState) -> GraphNodes:
    """_answer.answer()가 평면 answer/title/service_cards 를 반환하는 GraphNodes.

    실제 AnswerAgent 는 {**state, "answer": ..., "title": ..., "service_cards": ...}
    평면 키를 반환한다(answer_node 가 이를 읽어 output 채널로 매핑). 테스트 fixture 는
    make_agent_state(output 중첩)로 조립하므로, 여기서 output 채널을 평면 키로 풀어
    실제 에이전트 반환 계약을 모사한다.
    """
    nodes = GraphNodes.__new__(GraphNodes)
    out = returned_state.get("output", {})
    flat = {
        **returned_state,
        "answer": out.get("answer"),
        "title": out.get("title"),
        "service_cards": out.get("service_cards"),
    }
    answer = MagicMock()
    answer.answer = AsyncMock(return_value=flat)
    nodes._answer = answer
    return nodes


def _count_cards_empty_warnings(mock_warning: MagicMock) -> int:
    return sum(
        1
        for call in mock_warning.call_args_list
        if call.args and "cards_empty_with_results" in str(call.args[0])
    )


@pytest.mark.asyncio
async def test_cards_empty_with_results_warns():
    """SQL_SEARCH + hydrated_services 있음 + service_cards=[] → warning 1회."""
    state = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        hydrated_services=[{"service_id": "A"}, {"service_id": "B"}],
    )
    returned = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        answer="안내드립니다.",
        title="수영장 안내",
        service_cards=[],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        result = await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 1
    # 반환값은 그대로 보존 (동작 변경 없음).
    assert result["output"]["answer"] == "안내드립니다."
    assert result["output"]["service_cards"] == []
    assert result["output"]["title"] == "수영장 안내"


@pytest.mark.asyncio
async def test_vector_search_cards_empty_with_results_warns():
    """VECTOR_SEARCH + hydrated_services 있음 + service_cards=[] → warning 1회.

    SQL_SEARCH 와 동일하게 VECTOR_SEARCH 경로도 관측 대상임을 봉인한다.
    """
    state = make_agent_state(
        intent=IntentType.VECTOR_SEARCH,
        hydrated_services=[{"service_id": "A"}],
    )
    returned = make_agent_state(
        intent=IntentType.VECTOR_SEARCH,
        answer="안내",
        title="유사 시설",
        service_cards=[],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        result = await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 1
    assert result["output"]["answer"] == "안내"
    assert result["output"]["service_cards"] == []
    assert result["output"]["title"] == "유사 시설"


@pytest.mark.asyncio
async def test_sql_results_only_cards_empty_warns():
    """hydrated_services 비고 sql_results 만 있음 + service_cards=None → warning 1회.

    sql_results 분기(hydrated 없이도 결과가 있는 경우)를 봉인한다.
    """
    state = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        hydrated_services=[],
        sql_results=[{"service_id": "A"}, {"service_id": "B"}],
    )
    returned = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        answer="안내",
        service_cards=None,
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        result = await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 1
    assert result["output"]["service_cards"] is None


@pytest.mark.asyncio
async def test_error_answer_fast_path_skips_observation():
    """error+answer fast-path → _answer.answer() 미호출, 관측 블록도 안 탐.

    LLM 미호출 경로에서는 관측이 동작하지 않아야 한다(warning 0회).
    """
    state = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        hydrated_services=[{"service_id": "A"}],
        error="upstream boom",
        answer="죄송합니다, 오류가 발생했습니다.",
    )
    # answer() 가 호출되면 테스트 실패하도록 side_effect 로 강제.
    nodes = GraphNodes.__new__(GraphNodes)
    answer = MagicMock()
    answer.answer = AsyncMock(side_effect=AssertionError("answer() must not be called"))
    nodes._answer = answer

    with patch("agents.nodes.logger.warning") as mock_warning:
        result = await nodes.answer_node(state)

    answer.answer.assert_not_awaited()
    assert _count_cards_empty_warnings(mock_warning) == 0
    # fast-path 는 answer/title/service_cards 데이터를 만들지 않는다 (node_path 만 누적).
    assert set(result) <= {"node_path"}


@pytest.mark.asyncio
async def test_cards_present_no_warning():
    """정상 케이스(service_cards 비어있지 않음) → warning 미발생."""
    state = make_agent_state(
        intent=IntentType.VECTOR_SEARCH,
        hydrated_services=[{"service_id": "A"}],
    )
    returned = make_agent_state(
        intent=IntentType.VECTOR_SEARCH,
        answer="안내",
        service_cards=[{"service_id": "A"}],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 0


@pytest.mark.asyncio
async def test_no_results_no_warning():
    """검색 결과 진짜 0건 → warning 미발생(정상적인 결과 없음)."""
    state = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        hydrated_services=[],
        sql_results=[],
    )
    returned = make_agent_state(
        intent=IntentType.SQL_SEARCH,
        answer="결과가 없습니다.",
        service_cards=[],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 0


@pytest.mark.asyncio
async def test_fallback_intent_not_checked():
    """FALLBACK intent → 검사 대상 아님, warning 미발생."""
    state = make_agent_state(
        intent=IntentType.FALLBACK,
        hydrated_services=[{"service_id": "A"}],
    )
    returned = make_agent_state(
        intent=IntentType.FALLBACK,
        answer="안내 메시지",
        service_cards=[],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 0


@pytest.mark.asyncio
async def test_analytics_intent_not_checked():
    """ANALYTICS intent → 검사 대상 아님, warning 미발생."""
    state = make_agent_state(
        intent=IntentType.ANALYTICS,
        sql_results=[{"x": 1}],
    )
    returned = make_agent_state(
        intent=IntentType.ANALYTICS,
        answer="집계 결과",
        service_cards=[],
    )
    nodes = _make_nodes(returned)

    with patch("agents.nodes.logger.warning") as mock_warning:
        await nodes.answer_node(state)

    assert _count_cards_empty_warnings(mock_warning) == 0
