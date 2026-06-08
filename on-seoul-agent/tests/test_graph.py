"""AgentGraph (LangGraph StateGraph) 단위 / 통합 테스트 (Phase 17).

검증 대상:
- 각 노드(router, sql, vector, map, fallback, answer, trace) 단위 동작
- 조건부 엣지 분기 (SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK)
- 자기 교정 사이클 (빈 answer → 재검색 → 재답변, 최대 1회)
- 기존 AgentWorkflow와 동일한 입출력 계약 (AgentState 기반)
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent, _TitleOutput
from agents.graph import AgentGraph
from agents.nodes import (
    GraphNodes,
    _ANALYTICS_DROP_ORDER,
    _MAP_RETRY_RADIUS_M,
)
from agents.router_agent import RouterAgent, _IntentOutput
from agents.vector_agent import VectorAgent, _RefinedQuery
from core.exceptions import RateLimitException
from schemas.state import AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_analytics_agent,
    make_answer_agent,
    make_router,
    make_sql_agent,
    patch_node_sessions,
    run_graph,
    stream_graph,
)


# ---------------------------------------------------------------------------
# 픽스처 헬퍼 — 이 파일 전용
# ---------------------------------------------------------------------------


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


# 공유 헬퍼 별칭 (가독성)
_router = make_router
_sql_agent = make_sql_agent
_answer_agent = make_answer_agent
_ai_session = make_ai_session


def _vector_agent(rows: list[dict]) -> tuple[VectorAgent, MagicMock, AsyncMock]:
    """rows 를 vector_search 결과로 반환하는 VectorAgent mock + ai_session + bm25 mock."""
    agent = VectorAgent.__new__(VectorAgent)

    refine_chain = MagicMock()
    refine_chain.ainvoke = AsyncMock(
        return_value=_RefinedQuery(
            refined_query="정제된 질의",
            max_class_name=None,
            area_name=None,
            service_status=None,
        )
    )
    agent._refine_chain = refine_chain

    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
    agent._embeddings = embeddings

    # __new__ 가 __init__ 을 건너뛰므로 _channel_sema 를 직접 설정한다.
    agent._channel_sema = asyncio.Semaphore(4)

    mock_bm25 = AsyncMock(return_value=[])
    return agent, make_ai_session(), mock_bm25


# ---------------------------------------------------------------------------
# 1. 조건부 엣지 분기 테스트
# ---------------------------------------------------------------------------


class TestConditionalEdgeRouting:
    async def test_sql_search_route(self):
        """SQL_SEARCH intent → sql_node 실행, sql_results 채워짐."""
        rows = [{"service_id": "S001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.SQL_SEARCH
        assert result["sql_results"] is not None
        assert any(r["service_id"] == "S001" for r in result["sql_results"])
        assert result["answer"] == "수영장 안내입니다."
        assert result["error"] is None

    async def test_vector_search_route(self):
        """VECTOR_SEARCH intent → vector_node 실행, HydrationNode hydration, vector_results 채워짐."""
        rows = [{"service_id": "V001", "service_name": "체험관", "similarity": 0.9}]
        vector_agent, ai_session, mock_bm25 = _vector_agent(rows)
        hydrated = [
            {"service_id": "V001", "service_name": "체험관", "service_status": "접수중"}
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=rows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="아이랑 체험할 수 있는 곳"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        assert result["intent"] == IntentType.VECTOR_SEARCH
        assert result["vector_results"] is not None
        assert any(r["service_id"] == "V001" for r in result["vector_results"])
        assert result["answer"] == "체험관 안내입니다."
        assert result["error"] is None

    async def test_map_route_with_coords(self):
        """MAP intent + lat/lng + 반경 내 결과 있음 → map_node 1회 실행, 재시도 없음."""
        geojson = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"service_id": "M1"}}],
        }
        _, data_session = _sql_agent([])

        with patch("agents.nodes.map_search", return_value=geojson) as mock_map:
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent("주변 시설입니다."),
            )
            result = await run_graph(
                graph,
                _state(user_lat=37.5665, user_lng=126.9780),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        assert result["intent"] == IntentType.MAP
        assert result["map_results"] == geojson
        # 결과가 있으므로 재시도 없이 기본 반경으로 1회만 호출된다.
        mock_map.assert_awaited_once_with(
            data_session, 37.5665, 126.9780, radius_m=1000
        )

    async def test_map_route_without_coords_falls_back(self):
        """MAP intent + lat/lng 없음 → map_results=None, map_fallback 처리."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.MAP),
            answer_agent=_answer_agent("위치 정보가 없습니다."),
        )
        result = await run_graph(
            graph,
            _state(user_lat=None, user_lng=None),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.MAP
        assert result["map_results"] is None

    async def test_fallback_route_skips_search(self):
        """FALLBACK intent → 검색 없이 answer_node로 바로 이동."""
        sql_agent, data_session = _sql_agent([])
        vector_agent, _, mock_bm25 = _vector_agent([])

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            sql_agent=sql_agent,
            vector_agent=vector_agent,
            answer_agent=_answer_agent("안내 메시지입니다."),
        )
        result = await run_graph(
            graph,
            _state(message="안녕하세요"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.FALLBACK
        assert result["sql_results"] is None
        assert result["vector_results"] is None
        assert result["answer"] == "안내 메시지입니다."
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# 1b. ANALYTICS intent 경로 (analytics_node → answer_node, hydration 없음)
# ---------------------------------------------------------------------------


class TestAnalyticsRoute:
    async def test_analytics_route_reaches_answer(self):
        """ANALYTICS intent → analytics_node 실행 → answer_node 도달."""
        rows = [{"group_value": "강서구", "count": 7}]
        analytics_agent, data_session = make_analytics_agent(
            rows, group_by="area_name", keyword="테니스장"
        )
        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent("강서구에 가장 많습니다."),
        )
        result = await run_graph(
            graph,
            _state(message="테니스장 자치구별 분포"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.ANALYTICS
        assert result["analytics_results"] == rows
        assert result["analytics_group_by"] == "area_name"
        assert result["analytics_metric"] == "count"
        assert result["answer"] == "강서구에 가장 많습니다."
        assert result["error"] is None

    async def test_analytics_skips_hydration_and_search_persist(self):
        """analytics_node 는 hydration 을 거치지 않고 search_persist 채널을 쓰지 않는다."""
        rows = [{"group_value": "체육시설", "count": 12}]
        analytics_agent, data_session = make_analytics_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent("요약"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        # search_channels 미사용 → search_persist 는 chat_search_* 적재를 건너뛴다.
        all_sqls = [str(c[0][0]) for c in ai_session.execute.call_args_list]
        assert not any("chat_search_results" in sql for sql in all_sqls)
        assert not any("chat_search_queries" in sql for sql in all_sqls)
        # trace 는 종단 노드로 항상 적재된다.
        assert any("chat_agent_traces" in sql for sql in all_sqls)
        assert result["analytics_results"] == rows

    async def test_analytics_node_graceful_degrade_on_exception(self):
        """analytics_node 내부 예외 시 빈 결과 + error 로 graceful degrade 한다."""
        analytics_agent, data_session = make_analytics_agent([])
        analytics_agent._chain.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent("그래도 답변"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["analytics_results"] == []
        assert result["error"] is not None
        # 예외에도 그래프는 종료되고 answer 가 채워진다.
        assert result["answer"]

    async def test_analytics_node_graceful_degrade_on_tool_db_error(self):
        """analytics_search(도구) 가 DB 오류를 던져도 빈 결과 + error + analytics_error
        node_path 로 graceful degrade 하고 answer_node 가 실행된다.

        graceful_degrade_on_exception 은 LLM 체인 예외를 검증하지만, 설계 문서가
        명시한 'KeyError/DB 오류' 는 도구 호출 경로의 예외다. 도구 자체를 강제로
        터뜨려 analytics_node 의 try/except 가 그 경로도 포착하는지 봉인한다.
        """
        analytics_agent, data_session = make_analytics_agent([], group_by="area_name")

        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent("그래도 답변"),
        )

        with patch(
            "agents.analytics_agent.analytics_search",
            AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            result = await run_graph(
                graph,
                _state(),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        assert result["analytics_results"] == []
        assert result["error"] is not None
        # 예외에도 answer_node 가 실행되어 답변이 채워진다.
        assert result["answer"] == "그래도 답변"
        # node_path 에 analytics_error 가 기록되고 정상 analytics_node 는 없다.
        path = result["node_path"]
        assert "analytics_error" in path
        assert "analytics_node" not in path
        assert "answer_node" in path
        # trace 의 analytics 블록은 빈 결과로 적재된다 (intent==ANALYTICS 유지).
        assert result["trace"]["analytics"]["result_count"] == 0

    async def test_analytics_block_persisted_to_trace(self):
        """trace_node 가 ANALYTICS 일 때 trace.analytics 블록을 적재한다."""
        rows = [{"group_value": "강남구", "count": 4}]
        analytics_agent, data_session = make_analytics_agent(
            rows, group_by="area_name", keyword="수영장"
        )
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent("요약"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        trace = result["trace"]
        assert "analytics" in trace
        analytics = trace["analytics"]
        assert analytics["group_by"] == "area_name"
        assert analytics["metric"] == "count"
        assert analytics["result_count"] == 1
        assert analytics["result"] == rows
        assert "filters" in analytics
        # MAJOR 1: AnalyticsAgent 가 추출한 키워드가 trace filters 에 보존돼야 한다
        # (ANALYTICS 경로는 sql_node 를 거치지 않으므로 analytics_keyword 슬롯으로 전달).
        assert analytics["filters"]["keyword"] == "수영장"


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

        assert result["answer"] == "답변"

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

        assert result["intent"] == IntentType.SQL_SEARCH
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


class TestSelfCorrectionCycle:
    async def test_empty_answer_triggers_retry(self):
        """answer가 빈 문자열이면 retry_count=0일 때 재검색(router로 복귀)을 시도한다."""
        rows = [{"service_id": "S001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)

        # 첫 번째 호출은 빈 답변, 두 번째 호출은 정상 답변 반환
        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(
            side_effect=[
                "",  # 첫 번째: 빈 답변 → 재시도 트리거
                "재검색 후 답변",  # 두 번째: 정상 답변
            ]
        )
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title="수영장 안내"))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=agent,
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # 재시도 후 최종 답변이 채워져야 한다
        assert result["answer"] == "재검색 후 답변"
        assert result["retry_count"] == 1

    async def test_self_correction_max_one_retry(self):
        """자기 교정은 최대 1회만 수행한다 (retry_count >= 1이면 trace_node로 진행)."""
        _, data_session = _sql_agent([])

        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        # 두 번 모두 빈 답변 반환 — 두 번째는 trace로 진행해야 한다
        answer_chain.ainvoke = AsyncMock(return_value="")
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=""))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=agent,
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # 무한 루프 없이 종료, retry_count는 1
        assert result["retry_count"] == 1

    async def test_error_state_with_fallback_answer_skips_retry(self):
        """router 예외 시 fallback_answer가 설정되므로 재시도 없이 trace_node로 진행한다.

        수정(Phase 17): needs_retry = not answer.strip() and retry_count == 0
        error + fallback_answer 조합은 이미 최선의 응답이므로 재시도 불필요.
        """
        _, data_session = _sql_agent([])

        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        # router가 예외를 던지면 _router_node 핸들러가 fallback_answer를 주입한다.
        structured.ainvoke = AsyncMock(
            side_effect=[
                RuntimeError("일시적 LLM 오류"),
                _IntentOutput(intent=IntentType.FALLBACK),
            ]
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent("재시도 후 답변"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # fallback_answer가 설정되어 재시도 없이 종료 — retry_count는 0 유지
        assert result["retry_count"] == 0
        assert result["answer"] is not None
        assert len(result["answer"]) > 0


# ---------------------------------------------------------------------------
# 4. AgentState 입출력 계약 (workflow.py와 동일)
# ---------------------------------------------------------------------------


class TestAgentStateContract:
    async def test_initial_fields_preserved(self):
        """run() 실행 후 room_id, message_id, message가 보존된다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(room_id=99, message_id=77, message="테스트"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["room_id"] == 99
        assert result["message_id"] == 77
        assert result["message"] == "테스트"

    async def test_result_has_all_typed_fields(self):
        """run() 결과 state에 AgentState의 모든 키(retry_count 포함)가 존재한다."""
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

        expected_keys = {
            "room_id",
            "message_id",
            "message",
            "title_needed",
            "intent",
            "user_lat",
            "user_lng",
            "refined_query",
            "sql_results",
            "vector_results",
            "map_results",
            "answer",
            "title",
            "trace",
            "error",
            "retry_count",
            "sql_keyword",
        }
        assert expected_keys <= set(result.keys())

    async def test_error_sets_fallback_answer(self):
        """Router 예외 시 error 필드와 fallback 답변이 채워진다."""
        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        structured.ainvoke = AsyncMock(side_effect=_raise)
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # router 예외 시 fallback answer가 설정되며, error 없이 정상 종료된다
        assert result["answer"] is not None
        assert len(result["answer"]) > 0

    async def test_title_generated_when_title_needed(self):
        """title_needed=True이면 title이 채워진다."""
        _, data_session = _sql_agent([])
        answer_agent = _answer_agent(title="수영장 조회")

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=answer_agent,
        )
        result = await run_graph(
            graph,
            _state(title_needed=True),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] == "수영장 조회"
        answer_agent._title_chain.ainvoke.assert_called_once()


# ---------------------------------------------------------------------------
# 5. stream() 검증
# ---------------------------------------------------------------------------


class TestAgentGraphStream:
    async def _collect(self, gen) -> list[tuple[str, object]]:
        events = []
        async for event_type, data in gen:
            events.append((event_type, data))
        return events

    async def test_stream_yields_progress_then_result(self):
        """stream()은 progress 이벤트들 후 result를 yield한다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent("답변"),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        types = [e for e, _ in events]
        assert "progress" in types
        assert types[-1] == "result"

    async def test_stream_result_has_answer(self):
        """stream() result 이벤트에 answer가 채워진다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("스트림 답변"),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert len(result_events) == 1
        _, result = result_events[0]
        assert result["answer"] == "스트림 답변"

    async def test_result_carries_service_cards_through_graph(self):
        """answer_node 가 AnswerAgent.service_cards 를 그래프 최종 state 로 전달한다.

        회귀: answer_node 래퍼가 answer/title 만 추출하고 service_cards 를
        누락하면, 단위 테스트(AnswerAgent.answer 직접 호출)는 통과해도 실제
        그래프 경로의 final payload 는 빈 배열이 된다. 이 통합 경로를 봉인한다.
        """
        rows = [
            {
                "service_id": "S001",
                "service_name": "수영장",
                "service_url": "https://x",
            },
            {
                "service_id": "S002",
                "service_name": "테니스장",
                "service_url": "https://y",
            },
        ]
        sql_agent, data_session = _sql_agent(rows)
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        cards = result.get("service_cards")
        assert cards, f"service_cards 가 그래프 최종 state 에 전달되지 않음: {cards!r}"
        assert {c["service_id"] for c in cards} == {"S001", "S002"}

    async def test_stream_progress_steps_routing_searching_answering(self):
        """progress 이벤트의 step에 routing, searching, answering이 포함된다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent(),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert "routing" in progress_steps
        assert "searching" in progress_steps
        assert "answering" in progress_steps

    async def test_stream_emits_re_searching_on_retry(self):
        """재시도(SQL 0건→VECTOR 전환) 시 re_searching progress 1회 + 검색/답변 이벤트 재흐름."""
        sql_agent, data_session = _sql_agent([])  # SQL 0건 → 재시도 유발
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        vrows = [{"service_id": "V9", "service_name": "체험관", "similarity": 0.8}]
        hydrated = [{"service_id": "V9", "service_name": "체험관"}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                router=_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            events = await self._collect(
                stream_graph(
                    graph, _state(), data_session=data_session, ai_session=ai_session
                )
            )

        steps = [d["step"] for t, d in events if t == "progress"]
        assert steps.count("re_searching") == 1
        # re_searching 이후 전환 경로의 searching/answering 이벤트가 다시 흐른다.
        idx = steps.index("re_searching")
        assert "searching" in steps[idx + 1 :]
        assert "answering" in steps[idx + 1 :]

    async def test_analytics_route_emits_searching_progress(self):
        """ANALYTICS 경로도 router 에서 'searching' progress 를 방출한다 (MAJOR 2).

        회귀: ANALYTICS 가 searching intent 튜플에서 누락되면 LLM 추출 + DB 집계가
        진행 중인데도 router_node 에서 조기에 answering 을 방출한다.
        """
        analytics_agent, data_session = make_analytics_agent(
            [{"group_value": "강남구", "count": 4}], group_by="area_name"
        )
        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent(),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert "searching" in progress_steps
        # searching 은 answering 보다 먼저 방출돼야 한다 (조기 answering 회귀 방어).
        assert progress_steps.index("searching") < progress_steps.index("answering")


# ---------------------------------------------------------------------------
# 6. DB 세션 라우팅 검증 (SQL → data_session, Vector → ai_session)
# ---------------------------------------------------------------------------


class TestSessionRouting:
    async def test_sql_uses_data_session_not_ai_session(self):
        """SQL_SEARCH에서 data_session만 SQL 조회에 사용된다."""
        sql_agent, data_session = _sql_agent([])
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        # ai_session.execute 는 search_persist + trace 에만 사용된다 (SQL 조회에는 미사용).
        # 각 호출의 SQL 내용으로 data_session이 아닌 ai_session에 올바른 쿼리가 갔는지 확인.
        all_sqls = [str(c[0][0]) for c in ai_session.execute.call_args_list]
        assert any("chat_agent_traces" in sql for sql in all_sqls)
        assert not any("public_service_reservations" in sql for sql in all_sqls)

    async def test_vector_uses_ai_session_not_data_session(self):
        """VECTOR_SEARCH에서 data_session.execute가 벡터 조회에 사용되지 않는다."""
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        data_session = MagicMock()
        data_session.execute = AsyncMock()

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            await run_graph(
                graph,
                _state(),
                data_session=data_session,
                ai_session=ai_session,
            )

        data_session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Self-Correction 무한 루프 회귀 테스트 (가설 검증)
# ---------------------------------------------------------------------------


class TestSelfCorrectionInfiniteLoopRegression:
    """router_error 경로에서 is_retry 탐지 실패로 retry_count가 0으로 고정되는 버그 회귀 방지.

    _router_node 예외 시 _node_path에 "router_error"만 추가되므로 is_retry("router" in path)가
    False를 반환한다. recursion_limit=10으로 무한 루프를 차단하고, 예외 핸들러가 fallback answer를
    주입하여 _self_correction_edge의 `not answer.strip()` 조건을 False로 만들어 종료한다.
    """

    async def test_router_always_failing_terminates_without_recursion_error(self):
        """router 가 예외를 던지면 fallback answer 가 설정되어 1 cycle 만에 종료된다.

        실제 동작: _router_node 예외 핸들러가 fallback answer 를 state 에 주입하므로
        _self_correction_edge 의 `not answer.strip()` 조건이 False 가 되어
        GraphRecursionError 없이 trace_node 로 즉시 이동한다.
        router_error 는 1회만 node_path 에 기록된다.
        """
        _, data_session = _sql_agent([])

        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        structured.ainvoke = AsyncMock(side_effect=_raise)
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent("불릴 일 없는 답"),
        )

        from agents.graph import _ACTIVE_NODES

        state = {**_state(), "retry_count": 0, "node_path": [], "started_at": None}

        token = _ACTIVE_NODES.set(graph._nodes)
        try:
            result = await AgentGraph._compiled_graph.ainvoke(
                state,
                config={
                    "recursion_limit": 8,
                    "configurable": {
                        "data_session": data_session,
                        "ai_session": _ai_session(),
                    },
                },
            )
        finally:
            _ACTIVE_NODES.reset(token)

        # fallback answer 가 설정되어 정상 종료된다.
        assert result["answer"], "fallback answer 가 비어있으면 안 된다"
        # triage_error(또는 구버전 router_error) 는 1회만 기록된다 (무한 사이클 없음).
        # W2: triage_node 가 router_node 를 대체하므로 triage_error 를 체크한다.
        error_count = (
            result["node_path"].count("triage_error")
            + result["node_path"].count("router_error")
        )
        assert error_count == 1, (
            f"triage_error/router_error 가 1회 초과 기록됨: {result['node_path']}"
        )

    async def test_retry_prep_node_increments_retry_count_and_clears_results(self):
        """retry_prep_node가 retry_count를 1 증가시키고 이전 검색 결과를 초기화한다.

        재시도 제어는 retry_count 단일 필드로 자기 완결되며,
        _node_path 기반 재진입 감지에 의존하지 않는다.
        """
        graph = AgentGraph(answer_agent=_answer_agent())

        stale_state: AgentState = {
            **_state(),
            "retry_count": 0,
            "sql_results": [{"service_id": "S001"}],
            "vector_results": [{"service_id": "S002"}],
            "map_results": {"type": "FeatureCollection"},
            "refined_query": "테니스장",
            "error": "이전 에러",
        }

        result = await graph._nodes.retry_prep_node(stale_state)

        # retry_count 증가
        assert result["retry_count"] == 1
        # 이전 검색 결과 및 error 초기화
        assert result["sql_results"] is None
        assert result["vector_results"] is None
        assert result["map_results"] is None
        assert result["refined_query"] is None
        assert result["error"] is None
        # node_path 기록 (반환 dict 누적분)
        assert "retry_prep" in result["node_path"]

    async def test_self_correction_edge_skips_retry_when_answer_present(self):
        """수정(Phase 17): answer가 있으면 error 유무와 무관하게 trace_node로 진행한다.

        needs_retry = not answer.strip() and retry_count == 0
        — error + fallback_answer 조합은 재시도 불필요.
        """
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )

        # answer 있고 error 있는 state — 수정 후: trace_node 로 바로 진행
        state_with_error: AgentState = {
            **_state(),
            "answer": "fallback message",
            "error": "still failing",
            "retry_count": 0,
        }

        # 수정 후: answer.strip() 이 truthy 이므로 needs_retry=False → end_normal
        assert graph._nodes.self_correction_edge(state_with_error) == "end_normal"

        # answer 없을 때만 retry 트리거
        state_empty_answer: AgentState = {
            **_state(),
            "answer": "",
            "error": None,
            "retry_count": 0,
        }
        assert (
            graph._nodes.self_correction_edge(state_empty_answer) == "retry_prep_node"
        )

        # retry_count >= 1 이면 항상 end_normal
        state_after_retry = {**state_empty_answer, "retry_count": 1}
        assert graph._nodes.self_correction_edge(state_after_retry) == "end_normal"

    async def test_self_correction_edge_zero_hits_triggers_retry(self):
        """SQL/VECTOR 하드 필터 0건이면 answer가 있어도 1회 재시도(케이스1 안전망)."""
        graph = AgentGraph(answer_agent=_answer_agent())

        zero_hits: AgentState = {
            **_state(),
            "intent": IntentType.SQL_SEARCH,
            "answer": "조건에 맞는 결과가 없어요.",
            "hydrated_services": [],
            "sql_results": [],
            "vector_results": None,
            "retry_count": 0,
        }
        assert graph._nodes.self_correction_edge(zero_hits) == "retry_prep_node"

    async def test_self_correction_edge_zero_hits_capped_after_retry(self):
        """0건이라도 retry_count>=1이면 무한루프 방지 — end_normal."""
        graph = AgentGraph(answer_agent=_answer_agent())
        zero_hits: AgentState = {
            **_state(),
            "intent": IntentType.VECTOR_SEARCH,
            "answer": "결과 없음",
            "hydrated_services": [],
            "retry_count": 1,
        }
        assert graph._nodes.self_correction_edge(zero_hits) == "end_normal"

    async def test_self_correction_edge_zero_hits_only_for_search_intents(self):
        """FALLBACK 등 비검색 intent는 0건이어도 재시도하지 않는다."""
        graph = AgentGraph(answer_agent=_answer_agent())
        state = {
            **_state(),
            "intent": IntentType.FALLBACK,
            "answer": "안내드립니다.",
            "hydrated_services": [],
            "retry_count": 0,
        }
        assert graph._nodes.self_correction_edge(state) == "end_normal"

    async def test_self_correction_edge_with_hits_no_retry(self):
        """결과가 있으면 재시도하지 않는다."""
        graph = AgentGraph(answer_agent=_answer_agent())
        state = {
            **_state(),
            "intent": IntentType.SQL_SEARCH,
            "answer": "5건 안내",
            "hydrated_services": [{"service_id": "S1"}],
            "retry_count": 0,
        }
        assert graph._nodes.self_correction_edge(state) == "end_normal"

    async def test_retry_prep_node_relaxes_payment_and_sets_flag(self):
        """retry_prep_node가 payment_type을 드롭하고 retry_relaxed=True를 세팅한다."""
        graph = AgentGraph(answer_agent=_answer_agent())

        stale: AgentState = {
            **_state(),
            "retry_count": 0,
            "payment_type": "무료",
            "hydrated_services": [],
        }
        result = await graph._nodes.retry_prep_node(stale)
        assert result["payment_type"] is None
        assert result["retry_relaxed"] is True

    async def test_router_node_propagates_payment_type(self):
        """router_node 반환 update에 payment_type이 포함된다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.SQL_SEARCH,
                refined_query="강남구 무료 문화행사",
                max_class_name="문화체험",
                area_name="강남구",
                payment_type="무료",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state(message="강남구 무료 문화행사"))
        assert update["payment_type"] == "무료"

    async def test_router_node_omits_payment_type_when_none(self):
        """payment 미언급 시 payment_type 키를 update에 포함하지 않는다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(intent=IntentType.SQL_SEARCH)
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm
        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state())
        assert "payment_type" not in update


# ---------------------------------------------------------------------------
# 7a-bis. 방향성 self-correction 재시도 (forced_intent / ANALYTICS 드롭 / MAP 반경)
# ---------------------------------------------------------------------------


class TestDirectedSelfCorrectionRetry:
    """방향성 재시도: SQL→VECTOR 강제 전환, ANALYTICS 필터 드롭, MAP 반경 확장."""

    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer_agent())._nodes

    # ── 강제 전환 (3a~3c) ──

    async def test_retry_prep_sql_forces_vector_and_clears_filters(self):
        """SQL_SEARCH 0건 재시도 → forced_intent=VECTOR_SEARCH, 정형 필터 전부 None."""
        nodes = self._nodes()
        stale: AgentState = {
            **_state(),
            "intent": IntentType.SQL_SEARCH,
            "retry_count": 0,
            "max_class_name": "체육시설",
            "area_name": "강남구",
            "service_status": "접수중",
            "payment_type": "무료",
            "sql_results": [],
        }
        result = await nodes.retry_prep_node(stale)
        assert result["forced_intent"] == IntentType.VECTOR_SEARCH
        assert result["retry_count"] == 1
        for f in ("max_class_name", "area_name", "service_status", "payment_type"):
            assert result[f] is None
        assert result["retry_relaxed"] is True
        assert "retry_prep" in result["node_path"]

    async def test_router_node_honors_forced_intent_without_classify(self):
        """forced_intent 가 있으면 classify 미호출, intent 반환 + forced_intent=None 소비."""
        router = make_router(IntentType.SQL_SEARCH)  # classify 호출 시 SQL 반환(잘못된)
        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        structured = router._llm.with_structured_output.return_value

        update = await graph._nodes.router_node(
            _state(forced_intent=IntentType.VECTOR_SEARCH)
        )
        assert update["intent"] == IntentType.VECTOR_SEARCH
        assert update["forced_intent"] is None
        structured.ainvoke.assert_not_called()
        assert "router" in update["node_path"]

    async def test_e2e_sql_zero_hits_switches_to_vector(self):
        """SQL_SEARCH 0건 시나리오 → retry_prep → router(forced) → vector_node 경로 전환."""
        sql_agent, data_session = _sql_agent([])  # SQL 0건
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        vrows = [{"service_id": "V9", "service_name": "체험관", "similarity": 0.8}]
        hydrated = [{"service_id": "V9", "service_name": "체험관"}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                router=_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=data_session,
                ai_session=ai_session,
            )

        path = result["node_path"]
        assert "sql_node" in path
        assert "retry_prep" in path
        assert "vector_node" in path
        assert (
            path.index("sql_node")
            < path.index("retry_prep")
            < path.index("vector_node")
        )
        assert result["retry_count"] == 1

    # ── ANALYTICS 완화 (3c~3d) ──

    def test_analytics_zero_hits_predicate(self):
        nodes = self._nodes()
        assert nodes._analytics_zero_hits(_state(analytics_results=[])) is True
        assert nodes._analytics_zero_hits(_state(analytics_results=None)) is True
        assert (
            nodes._analytics_zero_hits(
                _state(analytics_results=[{"x": 1}], error="boom")
            )
            is True
        )
        assert nodes._analytics_zero_hits(_state(analytics_results=[{"x": 1}])) is False

    def test_self_correction_edge_analytics_zero_triggers_retry(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            answer="결과 없음",
            analytics_results=[],
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "retry_prep_node"

    def test_self_correction_edge_analytics_capped(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            answer="결과 없음",
            analytics_results=[],
            retry_count=1,
        )
        assert nodes.self_correction_edge(state) == "end_normal"

    async def test_retry_prep_analytics_drops_status_first(self):
        """effective 필터 우선순위: status 가 1순위. keyword 는 드롭 대상이 아니다.

        analytics_keyword 는 LLM 이 message 에서 재추출하는 출력 전용 슬롯이라
        state 드롭이 무효 → _ANALYTICS_DROP_ORDER 에서 제외. keyword 보유 분석
        질의여도 곧장 실효성 있는 service_status 를 드롭해야 한다.
        """
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            retry_count=0,
            analytics_keyword="따릉이",
            service_status="접수중",
            area_name="강남구",
            max_class_name="체육시설",
        )
        result = await nodes.retry_prep_node(state)
        # keyword 는 드롭 대상 아님(키 미포함) — service_status 가 1순위로 드롭됨.
        assert "analytics_keyword" not in result
        assert result["service_status"] is None
        # area/max_class 는 유지(키 미포함)
        assert "area_name" not in result
        assert "max_class_name" not in result
        assert result["analytics_results"] is None

    async def test_retry_prep_analytics_drops_area_when_no_status(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            retry_count=0,
            analytics_keyword=None,
            service_status=None,
            area_name="강남구",
        )
        result = await nodes.retry_prep_node(state)
        assert result["area_name"] is None
        assert "service_status" not in result

    async def test_retry_prep_analytics_no_filter_to_drop(self):
        nodes = self._nodes()
        state = _state(intent=IntentType.ANALYTICS, retry_count=0)
        result = await nodes.retry_prep_node(state)
        # 드롭할 필터 없음 — analytics_results 만 리셋
        assert result["analytics_results"] is None
        for f in _ANALYTICS_DROP_ORDER:
            assert f not in result

    async def test_e2e_analytics_zero_hits_drops_status_filter(self):
        """ANALYTICS 0건(service_status 보유) → retry_prep → 재실행 시
        두 번째 analytics_search 호출이 service_status 없이(None) 수행되는지를
        await 인자 레벨로 검증한다(SQL/MAP E2E 와 동일 수준).
        """
        from agents.analytics_agent import AnalyticsAgent

        analytics_agent = AnalyticsAgent.__new__(AnalyticsAgent)
        from agents.analytics_agent import _AnalyticsParams

        chain = MagicMock()
        chain.ainvoke = AsyncMock(
            return_value=_AnalyticsParams(
                group_by="max_class_name",  # type: ignore[arg-type]
                metric="count",  # type: ignore[arg-type]
                keyword="따릉이",
            )
        )
        analytics_agent._chain = chain

        data_session = MagicMock()
        data_session.execute = AsyncMock(return_value=MagicMock())

        # 1차: 0건 → 재시도 트리거. 2차: 1건 → 종료.
        with patch(
            "agents.analytics_agent.analytics_search",
            AsyncMock(side_effect=[[], [{"group_value": "체육시설", "count": 3}]]),
        ) as mock_analytics:
            graph = AgentGraph(
                router=_router(IntentType.ANALYTICS),
                analytics_agent=analytics_agent,
                answer_agent=_answer_agent("집계 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(service_status="접수중", area_name="강남구"),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        path = result["node_path"]
        assert path.count("analytics_node") == 2, f"analytics_node 2회여야 함: {path}"
        assert "retry_prep" in path
        assert result["retry_count"] == 1

        # 1차 호출은 service_status 를 보유, 2차(재시도)는 None 으로 드롭된 채 호출.
        first_kwargs = mock_analytics.await_args_list[0].kwargs
        second_kwargs = mock_analytics.await_args_list[1].kwargs
        assert first_kwargs["service_status"] == "접수중"
        assert second_kwargs["service_status"] is None
        # keyword 는 매 실행 LLM 재추출이므로 드롭되지 않고 동일하게 유지된다.
        assert second_kwargs["keyword"] == "따릉이"
        # area_name 은 status 가 1순위로 드롭되었으므로 2차에도 유지된다.
        assert second_kwargs["area_name"] == "강남구"

    # ── MAP 반경 확장 (C1, 3c~3d) ──

    def test_map_zero_hits_predicate(self):
        nodes = self._nodes()
        assert nodes._map_zero_hits(_state(map_results=None)) is False
        assert (
            nodes._map_zero_hits(
                _state(map_results={"type": "FeatureCollection", "features": []})
            )
            is True
        )
        assert (
            nodes._map_zero_hits(
                _state(
                    map_results={
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature"}],
                    }
                )
            )
            is False
        )

    def test_self_correction_edge_map_zero_triggers_retry(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            answer="주변에 없어요",
            map_results={"type": "FeatureCollection", "features": []},
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "retry_prep_node"

    def test_self_correction_edge_map_no_coords_no_retry(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            answer="위치를 알려주세요",
            map_results=None,
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "end_normal"

    def test_self_correction_edge_map_capped(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            answer="주변에 없어요",
            map_results={"type": "FeatureCollection", "features": []},
            retry_count=1,
        )
        assert nodes.self_correction_edge(state) == "end_normal"

    async def test_retry_prep_map_expands_radius(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            retry_count=0,
            map_results={"type": "FeatureCollection", "features": []},
        )
        result = await nodes.retry_prep_node(state)
        assert result["retry_radius_m"] == _MAP_RETRY_RADIUS_M
        assert result["map_results"] is None
        assert result["retry_relaxed"] is True

    async def test_map_node_uses_retry_radius(self):
        nodes = self._nodes()
        data_session = MagicMock()
        geojson = {"type": "FeatureCollection", "features": []}
        with (
            patch(
                "agents.nodes.map_search", AsyncMock(return_value=geojson)
            ) as mock_map,
            patch_node_sessions(data_session=data_session),
        ):
            update = await nodes.map_node(
                _state(user_lat=37.5, user_lng=127.0, retry_radius_m=3000),
            )
        mock_map.assert_awaited_once_with(data_session, 37.5, 127.0, radius_m=3000)
        # ChannelData query_text/parameters 에 확장 반경(3000m)이 반영되어야 한다.
        ch = next(iter(update["search_channels"].values()))
        assert "r=3000m" in ch["query"]["query_text"]
        assert ch["query"]["parameters"]["radius_m"] == 3000

    async def test_map_node_default_radius_when_no_retry(self):
        nodes = self._nodes()
        data_session = MagicMock()
        geojson = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"service_id": "M1"}}],
        }
        with (
            patch(
                "agents.nodes.map_search", AsyncMock(return_value=geojson)
            ) as mock_map,
            patch_node_sessions(data_session=data_session),
        ):
            update = await nodes.map_node(_state(user_lat=37.5, user_lng=127.0))
        mock_map.assert_awaited_once_with(data_session, 37.5, 127.0, radius_m=1000)
        # ChannelData query_text 에 실제 반경 반영
        ch = next(iter(update["search_channels"].values()))
        assert "r=1000m" in ch["query"]["query_text"]

    async def test_e2e_map_zero_hits_expands_radius(self):
        """MAP 1km 0건 → retry_prep → router(MAP 재분류) → map_node 3km 재호출 E2E."""
        empty = {"type": "FeatureCollection", "features": []}
        found = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"service_id": "M1"}}],
        }
        _, data_session = _sql_agent([])

        with patch(
            "agents.nodes.map_search",
            AsyncMock(side_effect=[empty, found]),
        ) as mock_map:
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent("주변 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(user_lat=37.5, user_lng=127.0),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        path = result["node_path"]
        assert path.count("map_node") == 2, f"map_node 2회여야 함: {path}"
        assert "retry_prep" in path
        assert result["retry_count"] == 1
        # 1차는 1000m, 2차(재시도)는 3000m 으로 호출된다.
        first_radius = mock_map.await_args_list[0].kwargs["radius_m"]
        second_radius = mock_map.await_args_list[1].kwargs["radius_m"]
        assert first_radius == 1000
        assert second_radius == _MAP_RETRY_RADIUS_M

    # ── 트리거 평가 순서 (C3, 3d) ──

    def test_empty_answer_takes_priority_over_zero_hits(self):
        """빈 답변 ∧ 0건 동시 참 → ② 빈 답변 분기 먼저(여전히 retry_prep)."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            answer="",
            sql_results=[],
            hydrated_services=[],
            retry_count=0,
        )
        # 둘 다 retry 지만, 빈 답변이 intent 평가보다 먼저 매칭되는지 확인.
        assert nodes.self_correction_edge(state) == "retry_prep_node"

    def test_intent_branches_mutually_exclusive(self):
        """한 순회에 하나의 intent 분기만 평가된다(ANALYTICS 0건이어도 MAP 판정 무관)."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            answer="결과 없음",
            analytics_results=[{"x": 1}],  # ANALYTICS 0건 아님
            map_results={
                "type": "FeatureCollection",
                "features": [],
            },  # MAP 0건이지만 무시
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "end_normal"


# ---------------------------------------------------------------------------
# 7b. Router refined_query 산출 → state 전파 회귀
# ---------------------------------------------------------------------------


class TestRouterRefinedQueryPropagation:
    """Router가 산출한 refined_query가 state["refined_query"]로 전파되어
    이후 cache_check_node가 정확한 키 lookup을 수행할 수 있어야 한다.
    """

    async def test_router_node_sets_refined_query_on_state(self):
        """router_node 반환 update dict에 refined_query가 포함된다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.VECTOR_SEARCH,
                refined_query="서울 테니스장",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state(message="테니스장"))

        assert update["intent"] == IntentType.VECTOR_SEARCH
        assert update["refined_query"] == "서울 테니스장"

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

        assert update["max_class_name"] == "체육시설"
        assert update["area_name"] == "강남구"
        assert update["service_status"] == "접수중"

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

    async def test_router_node_omits_refined_query_when_none(self):
        """Router가 refined_query=None을 반환하면 update에 키가 포함되지 않아
        state의 기존 refined_query(예: retry 경로의 초기화 값)를 덮어쓰지 않는다.
        """
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.FALLBACK,
                refined_query=None,
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state())

        assert update["intent"] == IntentType.FALLBACK
        assert "refined_query" not in update


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

    async def test_vector_node_does_not_return_error_dict_on_rate_limit(self):
        """RateLimitException 발생 시 {"error": ...} dict를 반환하지 않고 예외를 전파한다."""
        vector_agent = VectorAgent.__new__(VectorAgent)
        vector_agent.search = AsyncMock(side_effect=RateLimitException("소진"))

        nodes = self._make_nodes(vector_agent)

        raised = False
        try:
            with patch_node_sessions(ai_session=_ai_session()):
                await nodes.vector_node(_state(intent=IntentType.VECTOR_SEARCH))
        except RateLimitException:
            raised = True

        assert raised, "RateLimitException이 전파되어야 한다"

    async def test_vector_node_wraps_generic_exception_as_error_dict(self):
        """일반 예외는 기존과 동일하게 {"error": ...} dict로 변환된다."""
        vector_agent = VectorAgent.__new__(VectorAgent)
        vector_agent.search = AsyncMock(side_effect=ValueError("일반 오류"))

        nodes = self._make_nodes(vector_agent)
        with patch_node_sessions(ai_session=_ai_session()):
            result = await nodes.vector_node(_state(intent=IntentType.VECTOR_SEARCH))

        assert "error" in result
        assert "일반 오류" in result["error"]
