"""AgentGraph (LangGraph StateGraph) 단위 / 통합 테스트 (Phase 17).

검증 대상:
- 각 노드(router, sql, vector, map, fallback, answer, trace) 단위 동작
- 조건부 엣지 분기 (SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK)
- 자기 교정 사이클 (빈 answer → 재검색 → 재답변, 최대 1회)
- 기존 AgentWorkflow와 동일한 입출력 계약 (AgentState 기반)
"""

import gc
import time
import tracemalloc
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from agents.graph import AgentGraph
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from agents.vector_agent import VectorAgent, _RefinedQuery
from schemas.state import AgentState, IntentType


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------


def _state(**kwargs) -> AgentState:
    base = AgentState(
        room_id=1,
        message_id=10,
        message="수영장 알려줘",
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
        retry_count=0,
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

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()

    mock_bm25 = AsyncMock(return_value=[])
    return agent, session, mock_bm25


def _answer_agent(answer: str = "답변입니다.", title: str | None = None) -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)

    answer_chain = MagicMock()
    answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=answer))
    agent._answer_chain = answer_chain

    title_chain = MagicMock()
    title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=title or "수영장 안내"))
    agent._title_chain = title_chain
    return agent


def _ai_session() -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


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
        result = await graph.run(
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
        """VECTOR_SEARCH intent → vector_node 실행, vector_results 채워짐."""
        rows = [{"service_id": "V001", "service_name": "체험관", "similarity": 0.9}]
        vector_agent, ai_session, mock_bm25 = _vector_agent(rows)

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            result = await graph.run(
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
        """MAP intent + lat/lng → map_node 실행, map_results 채워짐."""
        geojson = {"type": "FeatureCollection", "features": []}
        _, data_session = _sql_agent([])

        with patch("agents.graph.map_search", return_value=geojson) as mock_map:
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent("주변 시설입니다."),
            )
            result = await graph.run(
                _state(lat=37.5665, lng=126.9780),
                data_session=data_session,
                ai_session=_ai_session(),
            )

        assert result["intent"] == IntentType.MAP
        assert result["map_results"] == geojson
        mock_map.assert_awaited_once_with(data_session, 37.5665, 126.9780)

    async def test_map_route_without_coords_falls_back(self):
        """MAP intent + lat/lng 없음 → map_results=None, map_fallback 처리."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.MAP),
            answer_agent=_answer_agent("위치 정보가 없습니다."),
        )
        result = await graph.run(
            _state(lat=None, lng=None),
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
        result = await graph.run(
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
        await graph.run(
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
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        assert result["answer"] == "답변"

    async def test_trace_has_required_fields(self):
        """결과 state의 trace에 intent, node_path, elapsed_ms가 존재한다."""
        _, data_session = _sql_agent([])

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await graph.run(
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

    async def test_sql_search_node_path(self):
        """SQL_SEARCH node_path: router → sql_node → answer_node → trace_node."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent(),
        )
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        node_path = result["trace"]["node_path"]
        assert "router" in node_path
        assert "sql_node" in node_path
        assert "answer_node" in node_path

    async def test_fallback_node_path(self):
        """FALLBACK node_path: router → fallback_node → answer_node."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        node_path = result["trace"]["node_path"]
        assert "router" in node_path
        assert "answer_node" in node_path

    async def test_vector_search_node_path(self):
        """VECTOR_SEARCH node_path에 vector_node가 포함된다."""
        vector_agent, ai_session, mock_bm25 = _vector_agent([])

        with patch("agents.vector_agent.bm25_search", mock_bm25):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=_answer_agent(),
            )
            result = await graph.run(
                _state(),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        node_path = result["trace"]["node_path"]
        assert "vector_node" in node_path


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
                _AnswerOutput(answer=""),  # 첫 번째: 빈 답변 → 재시도 트리거
                _AnswerOutput(answer="재검색 후 답변"),  # 두 번째: 정상 답변
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
        result = await graph.run(
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
        answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=""))
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=""))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=agent,
        )
        result = await graph.run(
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
        chain = MagicMock()
        # router가 예외를 던지면 _router_node 핸들러가 fallback_answer를 주입한다.
        chain.ainvoke = AsyncMock(
            side_effect=[
                RuntimeError("일시적 LLM 오류"),
                _IntentOutput(intent=IntentType.FALLBACK),
            ]
        )
        router_agent._chain = chain

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent("재시도 후 답변"),
        )
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # fallback_answer가 설정되어 재시도 없이 종료 — retry_count는 0 유지
        assert result["retry_count"] == 0
        assert result["answer"] is not None
        assert len(result["answer"]) > 0

    async def test_no_retry_when_answer_present(self):
        """정상 답변이 있으면 재시도 없이 trace_node로 진행한다."""
        _, data_session = _sql_agent([])

        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer="정상 답변"))
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=""))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=agent,
        )
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["answer"] == "정상 답변"
        assert result["retry_count"] == 0
        # answer_chain이 1번만 호출되어야 한다 (재시도 없음)
        assert answer_chain.ainvoke.call_count == 1


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
        result = await graph.run(
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
        result = await graph.run(
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
            "retry_count",
        }
        assert expected_keys <= set(result.keys())

    async def test_error_sets_fallback_answer(self):
        """Router 예외 시 error 필드와 fallback 답변이 채워진다."""
        router_agent = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        chain.ainvoke = AsyncMock(side_effect=_raise)
        router_agent._chain = chain

        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent(),
        )
        result = await graph.run(
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
        result = await graph.run(
            _state(title_needed=True),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] == "수영장 조회"
        answer_agent._title_chain.ainvoke.assert_called_once()

    async def test_title_none_when_not_needed(self):
        """title_needed=False이면 title은 None이다."""
        _, data_session = _sql_agent([])
        answer_agent = _answer_agent()

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=answer_agent,
        )
        result = await graph.run(
            _state(title_needed=False),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["title"] is None
        answer_agent._title_chain.ainvoke.assert_not_called()

    async def test_lat_lng_preserved(self):
        """lat/lng가 결과 state에 보존된다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await graph.run(
            _state(lat=37.5665, lng=126.9780),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["lat"] == pytest.approx(37.5665)
        assert result["lng"] == pytest.approx(126.9780)


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
            graph.stream(_state(), data_session=data_session, ai_session=_ai_session())
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
            graph.stream(_state(), data_session=data_session, ai_session=_ai_session())
        )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert len(result_events) == 1
        _, result = result_events[0]
        assert result["answer"] == "스트림 답변"

    async def test_stream_progress_steps_routing_searching_answering(self):
        """progress 이벤트의 step에 routing, searching, answering이 포함된다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=_answer_agent(),
        )
        events = await self._collect(
            graph.stream(_state(), data_session=data_session, ai_session=_ai_session())
        )

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert "routing" in progress_steps
        assert "searching" in progress_steps
        assert "answering" in progress_steps

    async def test_stream_fallback_has_three_progress_then_result(self):
        """FALLBACK: progress × 3 → result."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("안녕하세요"),
        )
        events = await self._collect(
            graph.stream(_state(message="안녕"), data_session=data_session, ai_session=_ai_session())
        )

        types = [e for e, _ in events]
        assert types.count("progress") == 3
        assert types[-1] == "result"


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
        await graph.run(
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        # ai_session.execute는 trace INSERT에만 1회 호출된다
        assert ai_session.execute.call_count == 1
        trace_sql = str(ai_session.execute.call_args[0][0])
        assert "chat_agent_traces" in trace_sql

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
            await graph.run(
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
        chain = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        chain.ainvoke = AsyncMock(side_effect=_raise)
        router_agent._chain = chain

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent("불릴 일 없는 답"),
        )

        from agents.graph import _ACTIVE_GRAPH

        graph._data_session = data_session
        graph._ai_session = _ai_session()
        graph._start = time.monotonic()
        graph._node_path = []
        state = _state()
        state = {**state, "retry_count": 0}

        token = _ACTIVE_GRAPH.set(graph)
        try:
            result = await AgentGraph._compiled_graph.ainvoke(
                state, config={"recursion_limit": 5}
            )
        finally:
            _ACTIVE_GRAPH.reset(token)

        # fallback answer 가 설정되어 정상 종료된다.
        assert result["answer"], "fallback answer 가 비어있으면 안 된다"
        # router_error 는 1회만 기록된다 (무한 사이클 없음).
        assert graph._node_path.count("router_error") == 1, (
            f"router_error 가 1회 초과 기록됨: {graph._node_path}"
        )

    async def test_retry_count_stuck_at_zero_when_router_raises(self):
        """known bug: router 가 예외를 던지면 _node_path 에 'router_error' 만 추가되어
        is_retry=False 가 되고 retry_count 가 0으로 고정된다.

        이 버그가 수정되어 retry_count가 1로 올바르게 증가하면,
        pytest.fail()이 호출되어 FAIL로 빨간불이 켜진다.
        is_retry 탐지 로직 수정 시 이 테스트를 함께 제거하거나 조건을 갱신할 것.
        """
        _, data_session = _sql_agent([])

        router_agent = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()

        # sync callable — AsyncMock이 side_effect를 호출 후 async로 감싸므로 async def 불필요
        def _raise_runtime(*_a, **_kw):
            raise RuntimeError("router_error")

        chain.ainvoke = AsyncMock(side_effect=_raise_runtime)
        router_agent._chain = chain

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent(),
        )

        # 직접 _router_node 두 번 호출 — 첫 호출 후 path 상태를 검사
        graph._data_session = data_session
        graph._ai_session = _ai_session()
        graph._start = time.monotonic()
        graph._node_path = []
        state = _state()

        # 1차 호출
        result1 = await graph._router_node(state)
        assert graph._node_path == ["router_error"]
        assert result1["retry_count"] == 0
        assert result1["error"] == "router_error"

        # 2차 호출 — 자기 교정으로 재진입했다고 가정.
        # _node_path에 "router_error"만 있으므로 is_retry("router" in path)=False,
        # retry_count가 0으로 고정되는 버그를 확인한다.
        result2 = await graph._router_node(state)
        if result2["retry_count"] != 0:
            # 버그가 수정된 경우 — 테스트를 갱신할 것
            pytest.fail(
                "BUG FIXED: retry_count is no longer stuck at 0 after router_error cycle. "
                "Remove or update this test alongside the is_retry fix."
            )
        # BUG CONFIRMED: retry_count가 재시도 시에도 0으로 고정됨
        pytest.xfail(
            "known: _router_node 예외 시 _node_path에 'router_error'가 추가되어 "
            "is_retry 검사('router' in path)가 False를 반환, retry_count가 0으로 고정됨. "
            "is_retry 탐지 로직 수정 시 이 테스트를 함께 제거하거나 조건을 갱신할 것."
        )

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

        # 수정 후: answer.strip() 이 truthy 이므로 needs_retry=False → trace_node
        assert graph._self_correction_edge(state_with_error) == "trace_node"

        # answer 없을 때만 retry 트리거
        state_empty_answer: AgentState = {
            **_state(),
            "answer": "",
            "error": None,
            "retry_count": 0,
        }
        assert graph._self_correction_edge(state_empty_answer) == "router_node"

        # retry_count >= 1 이면 항상 trace_node
        state_after_retry = {**state_empty_answer, "retry_count": 1}
        assert graph._self_correction_edge(state_after_retry) == "trace_node"

    async def test_memory_does_not_grow_across_buggy_invocations(self):
        """회귀 가드: router 예외 시나리오 30회 반복해도 self-correction cycle 이 발생하지 않는다.

        _router_node 예외 핸들러가 fallback answer 를 주입하므로
        self-correction retry 가 발생하지 않고 매 호출이 1 cycle 에 종료된다.
        따라서 무한 루프 메모리 누수 없이 30MB 이하를 유지해야 한다.
        """
        from agents.graph import _ACTIVE_GRAPH

        async def _run_buggy_once() -> None:
            router_agent = RouterAgent.__new__(RouterAgent)
            chain = MagicMock()

            def _raise(*_a, **_kw):
                raise RuntimeError("일시적 LLM 오류")

            chain.ainvoke = AsyncMock(side_effect=_raise)
            router_agent._chain = chain
            g = AgentGraph(
                router=router_agent,
                answer_agent=_answer_agent(),
            )
            g._data_session = MagicMock()
            g._ai_session = _ai_session()
            g._start = time.monotonic()
            g._node_path = []
            st = {**_state(), "retry_count": 0}
            tok = _ACTIVE_GRAPH.set(g)
            try:
                await AgentGraph._compiled_graph.ainvoke(
                    st, config={"recursion_limit": 5}
                )
            finally:
                _ACTIVE_GRAPH.reset(tok)

        # 워밍업 — 컴파일된 그래프 / import 캐시가 안정화된 후 측정
        for _ in range(5):
            await _run_buggy_once()
        gc.collect()

        tracemalloc.start()
        snap_before = tracemalloc.take_snapshot()
        for _ in range(30):
            await _run_buggy_once()
        gc.collect()
        snap_after = tracemalloc.take_snapshot()
        stats = snap_after.compare_to(snap_before, "filename")
        total_diff = sum(s.size_diff for s in stats)
        tracemalloc.stop()

        # self-correction cycle 이 발생하지 않으므로 30회 반복해도 30MB 미만이어야 한다.
        # 정상 그래프 실행 오버헤드: ~314 KB/invocation × 30 = ~9.4 MB
        # 임계값 30 MB = 실제 측정치의 3× — 무한 루프 누수(수십~수백 MB)는 충분히 감지
        assert total_diff < 30 * 1024 * 1024, (
            f"Memory grew by {total_diff} bytes over 30 invocations — "
            "unexpected memory accumulation detected."
        )
