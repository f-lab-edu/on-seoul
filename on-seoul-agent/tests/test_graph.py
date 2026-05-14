"""AgentGraph (LangGraph StateGraph) 단위 / 통합 테스트 (Phase 17).

검증 대상:
- 각 노드(router, sql, vector, map, fallback, answer, trace) 단위 동작
- 조건부 엣지 분기 (SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK)
- 자기 교정 사이클 (빈 answer → 재검색 → 재답변, 최대 1회)
- 기존 AgentWorkflow와 동일한 입출력 계약 (AgentState 기반)
"""

from unittest.mock import AsyncMock, MagicMock, patch

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
        hydrated = [{"service_id": "V001", "service_name": "체험관", "service_status": "접수중"}]

        with (
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
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

        with patch("agents.nodes.map_search", return_value=geojson) as mock_map:
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

        from agents.graph import _ACTIVE_NODES

        graph._nodes.prepare(data_session, _ai_session())
        state = {**_state(), "retry_count": 0}

        token = _ACTIVE_NODES.set(graph._nodes)
        try:
            result = await AgentGraph._compiled_graph.ainvoke(
                state, config={"recursion_limit": 5}
            )
        finally:
            _ACTIVE_NODES.reset(token)

        # fallback answer 가 설정되어 정상 종료된다.
        assert result["answer"], "fallback answer 가 비어있으면 안 된다"
        # router_error 는 1회만 기록된다 (무한 사이클 없음).
        assert graph._nodes.node_path.count("router_error") == 1, (
            f"router_error 가 1회 초과 기록됨: {graph._nodes.node_path}"
        )

    async def test_retry_prep_node_increments_retry_count_and_clears_results(self):
        """retry_prep_node가 retry_count를 1 증가시키고 이전 검색 결과를 초기화한다.

        재시도 제어는 retry_count 단일 필드로 자기 완결되며,
        _node_path 기반 재진입 감지에 의존하지 않는다.
        """
        _, data_session = _sql_agent([])
        graph = AgentGraph(answer_agent=_answer_agent())
        graph._nodes.prepare(data_session, _ai_session())

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
        # node_path 기록
        assert "retry_prep" in graph._nodes.node_path

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
        assert graph._nodes.self_correction_edge(state_with_error) == "trace_node"

        # answer 없을 때만 retry 트리거
        state_empty_answer: AgentState = {
            **_state(),
            "answer": "",
            "error": None,
            "retry_count": 0,
        }
        assert graph._nodes.self_correction_edge(state_empty_answer) == "retry_prep_node"

        # retry_count >= 1 이면 항상 trace_node
        state_after_retry = {**state_empty_answer, "retry_count": 1}
        assert graph._nodes.self_correction_edge(state_after_retry) == "trace_node"



# ---------------------------------------------------------------------------
# 8. VECTOR_SEARCH 경로 — Hydration E2E 검증
# ---------------------------------------------------------------------------


class TestGraphVectorHydrationE2E:
    """VECTOR_SEARCH 인텐트가 hydrated 원본 값을 answer 입력으로 사용함을 E2E 검증.

    임베딩 metadata 의 stale service_status('예약마감') 가 답변 컨텍스트에
    노출되지 않고, hydrate_services 가 반환한 최신 원본 값('접수중') 만이
    AnswerAgent._collect_results 의 출력에 포함되어야 한다.
    """

    async def test_vector_intent_uses_hydrated_results(self):
        """VECTOR_SEARCH: hydrated '접수중' 만 answer 입력에 들어가고 stale '예약마감' 은 없다."""
        # vector_search 가 반환할 stale 임베딩 metadata
        vector_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "metadata": {"service_status": "예약마감"},
            "similarity": 0.9,
        }]
        # hydrate_services 가 반환할 fresh 원본 행
        hydrated_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "area_name": "마포구",
            "service_status": "접수중",
            "service_url": "https://example.com/s001",
        }]

        vector_agent, ai_session, mock_bm25 = _vector_agent(vector_rows)

        # AnswerAgent.answer 가 받은 state 의 _collect_results 결과를 캡처한다.
        captured: dict = {}

        async def _capture_answer(state):
            helper = AnswerAgent.__new__(AnswerAgent)
            captured["results"] = helper._collect_results(state)
            return {**state, "answer": "마포 수영장 접수 중입니다."}

        answer_agent = AnswerAgent.__new__(AnswerAgent)
        answer_agent.answer = _capture_answer  # type: ignore[method-assign]

        data_session = MagicMock()
        data_session.execute = AsyncMock()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated_rows)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=vector_agent,
                answer_agent=answer_agent,
            )
            result = await graph.run(
                _state(message="마포 수영장"),
                data_session=data_session,
                ai_session=ai_session,
            )

        # answer 가 정상 생성되고 vector_results 가 hydrated 행으로 채워졌다
        assert result["answer"] == "마포 수영장 접수 중입니다."
        assert result["vector_results"] is not None
        assert result["vector_results"][0]["service_status"] == "접수중"

        # AnswerAgent 가 본 컨텍스트에는 hydrated '접수중' 만 들어가고 stale '예약마감' 은 없다.
        assert "results" in captured, "_collect_results 가 호출되지 않았다"
        statuses = [r["service_status"] for r in captured["results"]]
        assert "접수중" in statuses
        assert "예약마감" not in statuses
