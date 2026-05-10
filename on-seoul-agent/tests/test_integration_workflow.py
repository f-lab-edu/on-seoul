"""Agent 및 워크플로우 통합 테스트 (Phase 16).

여러 컴포넌트를 연결하여 전체 데이터 흐름을 검증한다.
LLM 및 DB 호출은 Mock으로 처리한다.

검증 대상:
- Router → SQL Agent → Answer Agent 전체 흐름
- Router → Vector Agent (하이브리드: vector + BM25 → RRF) → Answer Agent 전체 흐름
- Router → Map Agent → Answer Agent 전체 흐름
- Router → Fallback → Answer Agent 전체 흐름
- AgentWorkflow.run() 입출력 계약 (AgentState 기반)
- 컴포넌트 간 데이터 전달 정확성
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from agents.vector_agent import VectorAgent, _RefinedQuery
from agents.workflow import AgentWorkflow
from schemas.state import AgentState, IntentType


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------


def _state(**kwargs) -> AgentState:
    base = AgentState(
        room_id=1,
        message_id=10,
        message="체육시설 알려줘",
        title_needed=False,
        intent=None,
        lat=None,
        lng=None,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )
    base.update(kwargs)
    return base


def _router(intent: IntentType) -> RouterAgent:
    agent = RouterAgent.__new__(RouterAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    agent._chain = chain
    return agent


def _sql_agent(rows: list[dict]) -> tuple[SqlAgent, MagicMock]:
    agent = SqlAgent.__new__(SqlAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_SqlParams())
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def _vector_agent(rows: list[dict]) -> tuple[VectorAgent, MagicMock, AsyncMock]:
    """VectorAgent 픽스처.

    bm25_search를 함께 패치하여 Mock session에서 AttributeError가 삼켜지는 것을 방지한다.
    반환값: (agent, session, mock_bm25)
    """
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
    embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1536)
    agent._embeddings = embeddings

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()

    mock_bm25 = AsyncMock(return_value=[])
    return agent, session, mock_bm25


def _answer_agent(answer: str = "테스트 답변입니다.", title: str = "테스트 제목") -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)
    answer_chain = MagicMock()
    answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=answer))
    agent._answer_chain = answer_chain
    title_chain = MagicMock()
    title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=title))
    agent._title_chain = title_chain
    return agent


def _ai_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# 1. Router → SQL Agent → Answer Agent 통합 흐름
# ---------------------------------------------------------------------------


class TestSqlSearchIntegration:
    async def test_sql_results_flow_through_to_answer(self):
        """SQL 검색 결과가 Answer Agent에 전달되고 최종 state에 보존된다."""
        rows = [
            {
                "service_id": "S001",
                "service_name": "강남 수영장",
                "area_name": "강남구",
                "service_status": "접수중",
                "service_url": "https://example.com/s001",
            }
        ]
        sql_agent, data_session = _sql_agent(rows)
        answer_agent = _answer_agent("강남구 수영장을 안내해드립니다.")

        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _state(message="강남구 수영장 알려줘"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.SQL_SEARCH
        assert result["sql_results"] == rows
        assert result["answer"] == "강남구 수영장을 안내해드립니다."
        assert result["vector_results"] is None
        assert result["error"] is None

    async def test_sql_empty_results_still_generates_answer(self):
        """SQL 검색 결과가 없어도 Answer Agent가 답변을 생성한다."""
        sql_agent, data_session = _sql_agent([])
        answer_agent = _answer_agent("조건에 맞는 시설을 찾지 못했습니다.")

        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["sql_results"] == []
        assert result["answer"] == "조건에 맞는 시설을 찾지 못했습니다."
        assert result["error"] is None

    async def test_sql_node_path_contains_router_sql_answer(self):
        """SQL_SEARCH node_path: router → sql_agent → answer."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["trace"]["node_path"] == ["router", "sql_agent", "answer"]

    async def test_sql_uses_data_session_not_ai_session(self):
        """SQL Agent는 data_session만 사용하고 ai_session을 SQL 조회에 사용하지 않는다."""
        sql_agent, data_session = _sql_agent([])
        ai_session = _ai_session()
        ai_execute_mock = ai_session.execute

        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        # ai_session.execute는 trace INSERT에만 1회 호출된다
        assert ai_execute_mock.call_count == 1
        trace_sql = str(ai_execute_mock.call_args[0][0])
        assert "chat_agent_traces" in trace_sql

    async def test_title_generated_when_title_needed_on_sql_path(self):
        """SQL_SEARCH 경로에서 title_needed=True이면 title이 생성된다."""
        _, data_session = _sql_agent([])
        answer_agent = _answer_agent(title="수영장 안내")

        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _state(title_needed=True),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] == "수영장 안내"
        answer_agent._title_chain.ainvoke.assert_called_once()

    async def test_no_title_when_not_first_message_on_sql_path(self):
        """SQL_SEARCH 경로에서 title_needed=False이면 title이 None이다."""
        _, data_session = _sql_agent([])
        answer_agent = _answer_agent()

        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _state(title_needed=False),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] is None
        answer_agent._title_chain.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Router → Vector Agent (하이브리드 검색 + RRF) → Answer Agent 통합 흐름
# ---------------------------------------------------------------------------


class TestVectorSearchIntegration:
    async def test_vector_results_flow_through_to_answer(self):
        """벡터 검색 결과(RRF 결합)가 Answer Agent에 전달되고 최종 state에 보존된다."""
        rows = [
            {
                "service_id": "V001",
                "service_name": "자연체험관",
                "similarity": 0.92,
            }
        ]
        vector_agent, ai_session, mock_bm25 = _vector_agent(rows)
        answer_agent = _answer_agent("자연체험관을 추천합니다.")

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=answer_agent,
            )
            result = await workflow.run(
                _state(message="아이랑 체험할 수 있는 곳"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        assert result["intent"] == IntentType.VECTOR_SEARCH
        assert result["vector_results"] is not None
        assert len(result["vector_results"]) >= 1
        assert any(r["service_id"] == "V001" for r in result["vector_results"])
        assert result["answer"] == "자연체험관을 추천합니다."
        assert result["sql_results"] is None
        assert result["error"] is None

    async def test_vector_refined_query_set_in_state(self):
        """VectorAgent가 정제된 질의를 state.refined_query에 저장한다."""
        vector_agent, ai_session, mock_bm25 = _vector_agent([])

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            result = await workflow.run(
                _state(message="조용한 운동 시설"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        assert result["refined_query"] == "정제된 질의"

    async def test_vector_uses_ai_session_not_data_session(self):
        """Vector Agent는 ai_session만 사용하고 data_session을 DB 조회에 사용하지 않는다."""
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        data_session = MagicMock()
        data_session.execute = AsyncMock()

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            await workflow.run(
                _state(),
                data_session=data_session,
                ai_session=ai_session,
            )

        data_session.execute.assert_not_called()

    async def test_rrf_merges_vector_and_bm25_results(self):
        """하이브리드 검색(vector + BM25 → RRF)이 단일 결과 리스트로 합쳐진다.

        bm25_search가 vector_search와 다른 service_id를 반환하면
        RRF 결합 결과에 두 항목이 모두 포함된다.
        """
        vector_rows = [{"service_id": "V001", "service_name": "벡터 시설", "similarity": 0.9}]
        bm25_rows = [{"service_id": "B001", "service_name": "BM25 시설", "bm25_score": 1.5}]

        vector_agent, ai_session, mock_bm25 = _vector_agent(vector_rows)
        mock_bm25.return_value = bm25_rows

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            result = await workflow.run(
                _state(message="체험 시설"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        service_ids = {r["service_id"] for r in result["vector_results"]}
        assert "V001" in service_ids
        assert "B001" in service_ids

    async def test_rrf_score_present_in_vector_results(self):
        """RRF 결합 결과의 각 항목에 rrf_score 필드가 존재한다."""
        rows = [{"service_id": "V001", "service_name": "수영장", "similarity": 0.88}]
        vector_agent, ai_session, mock_bm25 = _vector_agent(rows)

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            result = await workflow.run(
                _state(),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        for row in result["vector_results"]:
            assert "rrf_score" in row, f"rrf_score 없음: {row}"

    async def test_vector_node_path_contains_router_vector_answer(self):
        """VECTOR_SEARCH node_path: router → vector_agent → answer."""
        _, ai_session, mock_bm25 = _vector_agent([])
        va, _, mb = _vector_agent([])
        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=va,
                answer_agent=_answer_agent(),
            )
            result = await workflow.run(
                _state(),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        assert result["trace"]["node_path"] == ["router", "vector_agent", "answer"]


# ---------------------------------------------------------------------------
# 3. Router → Map → Answer Agent 통합 흐름
# ---------------------------------------------------------------------------


class TestMapSearchIntegration:
    async def test_map_with_coords_produces_map_results(self):
        """MAP intent + 좌표 제공 시 map_results가 채워지고 Answer Agent에 전달된다."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [126.978, 37.566]},
                    "properties": {"service_id": "M001", "service_name": "서울광장 체육관"},
                }
            ],
        }

        with patch("agents.workflow.map_search", return_value=geojson) as mock_map:
            _, data_session = _sql_agent([])
            workflow = AgentWorkflow(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent("주변 시설을 안내합니다."),
            )
            result = await workflow.run(
                _state(lat=37.5665, lng=126.9780),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        assert result["intent"] == IntentType.MAP
        assert result["map_results"] == geojson
        assert result["map_results"]["type"] == "FeatureCollection"
        assert result["answer"] == "주변 시설을 안내합니다."
        mock_map.assert_awaited_once_with(data_session, 37.5665, 126.9780)

    async def test_map_without_coords_falls_back_to_fallback(self):
        """MAP intent에서 lat/lng 없으면 map_results가 None이고 map_fallback이 node_path에 포함된다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.MAP),
            answer_agent=_answer_agent("위치 정보를 제공해 주세요."),
        )
        result = await workflow.run(
            _state(lat=None, lng=None),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["map_results"] is None
        assert "map_fallback" in result["trace"]["node_path"]

    async def test_map_node_path_with_coords(self):
        """MAP intent + 좌표: node_path에 map_search가 포함된다."""
        geojson = {"type": "FeatureCollection", "features": []}

        with patch("agents.workflow.map_search", return_value=geojson):
            _, data_session = _sql_agent([])
            workflow = AgentWorkflow(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent(),
            )
            result = await workflow.run(
                _state(lat=37.5, lng=127.0),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        assert result["trace"]["node_path"] == ["router", "map_search", "answer"]


# ---------------------------------------------------------------------------
# 4. Router → FALLBACK → Answer Agent 통합 흐름
# ---------------------------------------------------------------------------


class TestFallbackIntegration:
    async def test_fallback_skips_all_search_agents(self):
        """FALLBACK: SQL Agent, Vector Agent 모두 호출되지 않는다."""
        sql_agent, data_session = _sql_agent([])
        vector_agent, _, mock_bm25 = _vector_agent([])
        answer_agent = _answer_agent("서울시 공공서비스 예약 챗봇입니다.")

        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            sql_agent=sql_agent,
            vector_agent=vector_agent,
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _state(message="안녕하세요"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["intent"] == IntentType.FALLBACK
        assert result["sql_results"] is None
        assert result["vector_results"] is None
        assert result["answer"] == "서울시 공공서비스 예약 챗봇입니다."
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()

    async def test_fallback_node_path(self):
        """FALLBACK node_path: router → fallback → answer."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["trace"]["node_path"] == ["router", "fallback", "answer"]

    async def test_fallback_answer_not_empty(self):
        """FALLBACK 경로에서도 answer가 비어 있지 않다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("이용 방법을 안내해드립니다."),
        )
        result = await workflow.run(
            _state(message="챗봇 사용법 알려줘"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["answer"]
        assert len(result["answer"]) > 0


# ---------------------------------------------------------------------------
# 5. AgentWorkflow.run() 입출력 계약 (AgentState)
# ---------------------------------------------------------------------------


class TestAgentStateContract:
    async def test_initial_state_fields_preserved(self):
        """workflow.run() 실행 후 초기 state의 room_id, message_id, message가 보존된다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(room_id=42, message_id=99, message="테스트 메시지"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["room_id"] == 42
        assert result["message_id"] == 99
        assert result["message"] == "테스트 메시지"

    async def test_result_state_always_has_trace(self):
        """workflow.run() 결과 state에는 항상 trace 딕셔너리가 존재한다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["trace"] is not None
        assert "intent" in result["trace"]
        assert "node_path" in result["trace"]
        assert "elapsed_ms" in result["trace"]

    async def test_result_state_has_all_typed_fields(self):
        """workflow.run() 결과 state에 AgentState의 모든 키가 존재한다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
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
            "lat",
            "lng",
            "refined_query",
            "sql_results",
            "vector_results",
            "map_results",
            "answer",
            "title",
            "trace",
            "error",
        }
        assert expected_keys <= set(result.keys())

    async def test_elapsed_ms_is_non_negative_integer(self):
        """trace.elapsed_ms는 0 이상의 정수다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert isinstance(result["trace"]["elapsed_ms"], int)
        assert result["trace"]["elapsed_ms"] >= 0

    async def test_error_path_sets_error_field_and_fallback_answer(self):
        """Router 예외 발생 시 error 필드에 오류 메시지가 채워지고 fallback 답변이 제공된다."""
        router = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM 응답 오류"))
        router._chain = chain

        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(router=router, answer_agent=_answer_agent())
        result = await workflow.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["error"] is not None
        assert "LLM 응답 오류" in result["error"]
        assert result["answer"] is not None
        assert len(result["answer"]) > 0
        assert "error" in result["trace"]["node_path"]

    async def test_title_is_none_when_not_needed(self):
        """title_needed=False이면 title 필드는 None이다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(title_needed=False),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] is None

    async def test_lat_lng_preserved_in_result_state(self):
        """입력으로 제공된 lat/lng가 결과 state에 보존된다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await workflow.run(
            _state(lat=37.5665, lng=126.9780),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["lat"] == pytest.approx(37.5665)
        assert result["lng"] == pytest.approx(126.9780)


# ---------------------------------------------------------------------------
# 6. stream() 통합 검증
# ---------------------------------------------------------------------------


class TestWorkflowStreamIntegration:
    async def _collect_events(self, gen) -> list[tuple[str, object]]:
        events = []
        async for event_type, data in gen:
            events.append((event_type, data))
        return events

    async def test_sql_search_stream_event_sequence(self):
        """SQL_SEARCH 스트림: progress(routing) → progress(searching) → progress(answering) → result."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent("답변"),
        )
        events = await self._collect_events(
            workflow.stream(
                _state(),
                data_session=data_session,
                ai_session=_ai_session(),
            )
        )

        types = [t for t, _ in events]
        assert types == ["progress", "progress", "progress", "result"]

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert progress_steps == ["routing", "searching", "answering"]

    async def test_fallback_stream_includes_answer_in_result(self):
        """FALLBACK 스트림 result에 answer가 채워진다."""
        _, data_session = _sql_agent([])
        workflow = AgentWorkflow(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("안녕하세요!"),
        )
        events = await self._collect_events(
            workflow.stream(
                _state(message="안녕"),
                data_session=data_session,
                ai_session=_ai_session(),
            )
        )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert len(result_events) == 1
        _, result_state = result_events[0]
        assert result_state["answer"] == "안녕하세요!"
        assert result_state["intent"] == IntentType.FALLBACK

    async def test_vector_search_stream_result_has_vector_results(self):
        """VECTOR_SEARCH 스트림 result의 vector_results가 채워진다."""
        rows = [{"service_id": "V001", "service_name": "체험관", "similarity": 0.9}]
        vector_agent, ai_session, mock_bm25 = _vector_agent(rows)

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            workflow = AgentWorkflow(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내"),
            )
            events = await self._collect_events(
                workflow.stream(
                    _state(message="아이랑 체험"),
                    data_session=MagicMock(),
                    ai_session=ai_session,
                )
            )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert result_events
        _, result_state = result_events[0]
        assert result_state["vector_results"] is not None
        assert any(r["service_id"] == "V001" for r in result_state["vector_results"])

    async def test_stream_error_in_router_yields_progress_then_result_with_error(self):
        """Router 오류 시 progress(routing) 1개 후 error가 담긴 result가 온다."""
        router = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM 오류"))
        router._chain = chain

        _, data_session = _sql_agent([])
        ai_session = _ai_session()
        workflow = AgentWorkflow(router=router, answer_agent=_answer_agent())
        events = await self._collect_events(
            workflow.stream(
                _state(),
                data_session=data_session,
                ai_session=ai_session,
            )
        )

        types = [t for t, _ in events]
        assert types == ["progress", "result"]

        _, result_state = events[-1]
        assert result_state["error"] is not None
        ai_session.rollback.assert_called_once()
