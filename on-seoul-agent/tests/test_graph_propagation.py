"""AgentGraph trace_node / refined_query 전파 / RateLimit 전파 / emit 헬퍼 테스트.

test_graph.py 분할 산출 — TestTraceNode / TestRouterRefinedQueryPropagation /
TestVectorNodeRateLimitPropagation / TestEmitHelpersOutsideNodeContext.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.router_agent import RouterAgent, _IntentOutput
from agents.vector_agent import VectorAgent
from core.exceptions import RateLimitException
from schemas.state import IntentType
from tests.helpers import patch_node_sessions, run_graph
from tests._graph_support import (
    _ai_session,
    _answer_agent,
    _router,
    _sql_agent,
    _state,
)


# ---------------------------------------------------------------------------
# 2. trace_node 검증 (종단 노드)
# ---------------------------------------------------------------------------


class TestTraceNode:
    async def test_trace_saved_to_ai_session(self):
        """그래프 실행 후 ai_session.execute가 chat_agent_traces INSERT로 호출된다."""
        _, data_session = _sql_agent([])
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        await run_graph(
            graph,
            _state(message_id=42),
            data_session=data_session,
            ai_session=ai_session,
        )

        ai_session.execute.assert_called_once()
        call_args = ai_session.execute.call_args[0]
        sql_str = str(call_args[0])
        assert "chat_agent_traces" in sql_str
        bind = call_args[1]
        assert bind["message_id"] == 42

    async def test_trace_save_failure_does_not_raise(self):
        """trace 저장 실패해도 워크플로우 answer는 정상 반환된다."""
        _, data_session = _sql_agent([])
        ai_session = _ai_session()
        ai_session.execute = AsyncMock(side_effect=Exception)

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("답변"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        assert result["output"]["answer"] == "답변"

    async def test_non_analytics_intent_omits_analytics_block_from_trace(self):
        """ANALYTICS 가 아닌 intent 의 trace 에는 analytics 블록이 없어야 한다 (회귀).

        trace_node 의 analytics 블록 적재는 intent==ANALYTICS 가드 안에서만
        일어나야 하며, SQL_SEARCH/VECTOR_SEARCH/MAP/FALLBACK 에는 누출되면 안 된다.
        """
        rows = [{"service_id": "S001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["plan"]["intent"] == IntentType.SQL_SEARCH
        assert "analytics" not in result["trace"]

    async def test_trace_has_required_fields(self):
        """결과 state의 trace에 intent, node_path, elapsed_ms가 존재한다."""
        _, data_session = _sql_agent([])

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        trace = result["trace"]
        assert trace is not None
        assert "intent" in trace
        assert "node_path" in trace
        assert "elapsed_ms" in trace
        assert isinstance(trace["elapsed_ms"], int)
        assert trace["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# 3. 자기 교정(Self-Correction) 사이클 테스트
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7b. Router refined_query 산출 → state 전파 회귀
# ---------------------------------------------------------------------------


class TestRouterRefinedQueryPropagation:
    """Router가 산출한 refined_query가 state["plan"]["refined_query"]로 전파되어
    이후 cache_check_node가 정확한 키 lookup을 수행할 수 있어야 한다.
    """

    # intent+refined_query 전파는 test_graph_triage
    # .TestRouterNodeStatePropagation.test_router_node_sets_intent_and_plan 이
    # 더 완전하게(secondary_intent/filters 포함) 커버하므로 축소했다. 아래는
    # postfilter 채널 전파/None 생략의 real-RouterAgent 경로만 유지한다.

    async def test_router_node_propagates_postfilter_metadata(self):
        """router_node 반환 update에 max_class_name/area_name/service_status가 포함된다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.SQL_SEARCH,
                refined_query="강남구 체육시설 접수중",
                max_class_name="체육시설",
                area_name="강남구",
                service_status="접수중",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state(message="강남구 체육시설"))

        assert update["filters"]["max_class_name"] == ["체육시설"]
        assert update["filters"]["area_name"] == ["강남구"]
        assert update["filters"]["service_status"] == "접수중"

    async def test_router_node_omits_postfilter_when_none(self):
        """Router가 메타데이터=None을 반환하면 update에 키를 포함하지 않는다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.VECTOR_SEARCH,
                refined_query="체험 시설",
                max_class_name=None,
                area_name=None,
                service_status=None,
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state())

        assert "max_class_name" not in update
        assert "area_name" not in update
        assert "service_status" not in update

    # refined_query=None 생략은 test_router_node_omits_postfilter_when_none(동일 None-생략
    # 분기) + test_graph_triage.test_router_node_omits_none_fields 가 커버하므로 축소했다.


# ---------------------------------------------------------------------------
# RateLimitException 전파 테스트
# ---------------------------------------------------------------------------


# refined_query=None 생략은 test_router_node_omits_postfilter_when_none(동일 None-생략
# 분기) + test_graph_triage.test_router_node_omits_none_fields 가 커버하므로 축소했다.


# ---------------------------------------------------------------------------
# RateLimitException 전파 테스트
# ---------------------------------------------------------------------------


class TestVectorNodeRateLimitPropagation:
    """vector_node가 RateLimitException을 흡수하지 않고 re-raise하는지 검증."""

    def _make_nodes(self, vector_agent: VectorAgent) -> GraphNodes:
        """GraphNodes 인스턴스를 최소 의존성으로 생성한다."""
        return GraphNodes(
            router=_router(IntentType.VECTOR_SEARCH),
            sql_agent=MagicMock(),
            vector_agent=vector_agent,
            answer_agent=_answer_agent(),
            analytics_agent=MagicMock(),
        )

    async def test_vector_node_reraises_rate_limit_exception(self):
        """VectorAgent.search()가 RateLimitException을 던지면 vector_node가 re-raise한다."""
        vector_agent = VectorAgent.__new__(VectorAgent)
        vector_agent.search = AsyncMock(
            side_effect=RateLimitException("Gemini embed rate limit 소진")
        )

        nodes = self._make_nodes(vector_agent)

        with (
            patch_node_sessions(ai_session=_ai_session()),
            pytest.raises(RateLimitException, match="rate limit 소진"),
        ):
            await nodes.vector_node(_state(intent=IntentType.VECTOR_SEARCH))

    # "error dict 미반환 = 예외 전파"는 위 test_vector_node_reraises_rate_limit_exception
    # (pytest.raises)이 동일하게 단언하므로 축소했다. generic 예외 → error dict
    # 대비 케이스는 아래 유지(fail-open 분기 구분).

    async def test_vector_node_wraps_generic_exception_as_error_dict(self):
        """일반 예외는 기존과 동일하게 {"error": ...} dict로 변환된다."""
        vector_agent = VectorAgent.__new__(VectorAgent)
        vector_agent.search = AsyncMock(side_effect=ValueError("일반 오류"))

        nodes = self._make_nodes(vector_agent)
        with patch_node_sessions(ai_session=_ai_session()):
            result = await nodes.vector_node(_state(intent=IntentType.VECTOR_SEARCH))

        assert "error" in result
        assert "일반 오류" in result["error"]


# ---------------------------------------------------------------------------
# get_stream_writer 안전 래퍼 (agents/_helpers.py) — 노드 컨텍스트 밖 no-op
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# get_stream_writer 안전 래퍼 (agents/_helpers.py) — 노드 컨텍스트 밖 no-op
# ---------------------------------------------------------------------------


class TestEmitHelpersOutsideNodeContext:
    """노드 컨텍스트(runnable) 밖에서 emit 헬퍼가 크래시 없이 no-op 인지 검증.

    QA 회귀(작업 3): 단위 테스트가 노드를 직접 호출하거나 run()(비스트리밍 ainvoke)
    으로 그래프를 돌릴 때 get_stream_writer 가 RuntimeError/LookupError 를 던지면
    노드가 미처리 예외로 죽는다. _writer 가 이를 흡수해 emit 이 무해해야 한다.
    """

    def test_writer_returns_none_outside_runnable_context(self):
        """runnable 컨텍스트 밖에서 _writer() 는 None 을 반환한다(예외 흡수)."""
        from agents._helpers import _writer

        assert _writer() is None

    def test_emit_progress_is_noop_outside_context(self):
        """emit_progress 가 컨텍스트 밖에서 예외 없이 no-op."""
        from agents._helpers import emit_progress

        # 예외가 나면 테스트 실패. 반환값 없음(None).
        assert emit_progress("searching") is None
        assert emit_progress("answering") is None

    def test_emit_decision_is_noop_outside_context(self):
        """emit_decision 이 컨텍스트 밖에서 예외 없이 no-op."""
        from agents._helpers import emit_decision

        assert emit_decision("RETRIEVE", ["SQL_SEARCH"], "근거") is None

    # unknown step(컨텍스트 밖)도 writer=None 단락으로 동일하게 no-op(None) 반환이라
    # test_emit_progress_is_noop_outside_context 와 같은 분기의 입력 순열로 축소했다.
