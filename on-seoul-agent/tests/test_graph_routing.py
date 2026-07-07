"""AgentGraph 조건부 엣지 분기 / ANALYTICS 경로 / DB 세션 라우팅 테스트.

test_graph.py 분할 산출 — TestConditionalEdgeRouting / TestAnalyticsRoute /
TestSessionRouting.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from schemas.state import IntentType
from tests.helpers import make_analytics_agent, run_graph
from tests._graph_support import (
    _ai_session,
    _answer_agent,
    _router,
    _sql_agent,
    _state,
    _vector_agent,
)


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

        assert result["plan"]["intent"] == IntentType.SQL_SEARCH
        assert result["sql"]["results"] is not None
        assert any(r["service_id"] == "S001" for r in result["sql"]["results"])
        assert result["output"]["answer"] == "수영장 안내입니다."
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

        assert result["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert result["vector"]["results"] is not None
        assert any(r["service_id"] == "V001" for r in result["vector"]["results"])
        assert result["output"]["answer"] == "체험관 안내입니다."
        assert result["error"] is None

    async def test_map_route_with_coords(self):
        """MAP intent + lat/lng + 반경 내 결과 있음 → map_node 1회 실행, 재시도 없음."""
        geojson = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"service_id": "M1"}}],
        }
        _, data_session = _sql_agent([])

        with patch(
            "agents._ondata_gateway._map_search", return_value=geojson
        ) as mock_map:
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

        assert result["plan"]["intent"] == IntentType.MAP
        assert result["map"]["results"] == geojson
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

        assert result["plan"]["intent"] == IntentType.MAP
        assert result["map"]["results"] is None

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

        assert result["plan"]["intent"] == IntentType.FALLBACK
        assert result["sql"].get("results") is None
        assert result["vector"].get("results") is None
        assert result["output"]["answer"] == "안내 메시지입니다."
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# 1b. ANALYTICS intent 경로 (analytics_node → answer_node, hydration 없음)
# ---------------------------------------------------------------------------


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

        assert result["plan"]["intent"] == IntentType.ANALYTICS
        assert result["analytics"]["results"] == rows
        assert result["analytics"]["group_by"] == "area_name"
        assert result["analytics"]["metric"] == "count"
        assert result["output"]["answer"] == "강서구에 가장 많습니다."
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
        assert result["analytics"]["results"] == rows

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

        assert result["analytics"]["results"] == []
        assert result["error"] is not None
        # 예외에도 그래프는 종료되고 answer 가 채워진다.
        assert result["output"]["answer"]

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

        assert result["analytics"]["results"] == []
        assert result["error"] is not None
        # 예외에도 answer_node 가 실행되어 답변이 채워진다.
        assert result["output"]["answer"] == "그래도 답변"
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


# ---------------------------------------------------------------------------
# 6. DB 세션 라우팅 검증 (SQL → data_session, Vector → ai_session)
# ---------------------------------------------------------------------------


class TestSessionRouting:
    # SQL→data_session(ai_session 에 조회 미누출) 검증은 test_node_local_sessions
    # (세션 격리 전용 회귀 가드) + 모든 SQL 라우팅 테스트가 data_session 으로
    # 조회를 실행하는 것으로 커버되므로 축소했다. 역방향(Vector 가 data_session 에
    # 누출되지 않음) 격리는 아래 유지한다.

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
