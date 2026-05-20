"""그래프 end-to-end — intent별 search_channels 적재 검증 (Task 5).

AgentGraph.run() 을 실제로 실행하여 search_persist_node 가 의도한 채널 데이터를
ai_session 에 전달하는지, 각 intent 시나리오별로 queries / results 행 구조가
명세대로인지 검증한다.

검증 전략:
    ai_session.execute.call_args_list 를 순회하여
    "chat_search_queries" / "chat_search_results" 가 포함된 call 을 추출한다.
    trace_node 는 "chat_agent_traces" 를 INSERT 하므로 세 유형이 자연스럽게 분리된다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from agents.graph import AgentGraph
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from agents.vector_agent import VectorAgent, _RefinedQuery
from schemas.search import SearchChannel, SearchKind
from schemas.state import AgentState, IntentType
from tests.helpers import make_agent_state


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _router(intent: IntentType) -> RouterAgent:
    agent = RouterAgent.__new__(RouterAgent)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    return agent


def _sql_agent(rows: list[dict], keyword: str | None = None) -> tuple[SqlAgent, MagicMock]:
    """rows 를 반환하는 sql_agent + data_session 반환."""
    agent = SqlAgent.__new__(SqlAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_SqlParams(keyword=keyword))
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    data_session = MagicMock()
    data_session.execute = AsyncMock(return_value=mock_result)
    return agent, data_session


def _vector_agent(
    refined_query: str = "정제된 질의",
) -> VectorAgent:
    """vector_search / bm25_search / hydrate_services 는 patch 로 주입한다."""
    agent = VectorAgent.__new__(VectorAgent)
    refine_chain = MagicMock()
    refine_chain.ainvoke = AsyncMock(
        return_value=_RefinedQuery(
            refined_query=refined_query,
            max_class_name=None,
            area_name=None,
            service_status=None,
        )
    )
    agent._refine_chain = refine_chain
    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
    agent._embeddings = embeddings
    return agent


def _answer_agent(answer: str = "답변입니다.", title: str = "안내") -> AnswerAgent:
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
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _get_queries_rows(ai_session: MagicMock) -> list[dict] | None:
    """ai_session.execute 호출 중 chat_search_queries INSERT 에 전달된 rows 를 반환."""
    for call in ai_session.execute.call_args_list:
        if "chat_search_queries" in str(call.args[0]):
            return call.args[1]
    return None


def _get_results_rows(ai_session: MagicMock) -> list[dict] | None:
    """ai_session.execute 호출 중 chat_search_results INSERT 에 전달된 rows 를 반환."""
    for call in ai_session.execute.call_args_list:
        if "chat_search_results" in str(call.args[0]):
            return call.args[1]
    return None


def _has_trace_insert(ai_session: MagicMock) -> bool:
    """ai_session.execute 호출 중 chat_agent_traces INSERT 가 있는지 확인."""
    return any(
        "chat_agent_traces" in str(call.args[0])
        for call in ai_session.execute.call_args_list
    )


# ---------------------------------------------------------------------------
# 1. SQL_SEARCH intent
# ---------------------------------------------------------------------------


class TestSqlIntentPersist:
    async def test_sql_intent_inserts_sql_channel_query(self):
        """SQL_SEARCH → chat_search_queries 에 sql 채널 1행 INSERT."""
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내입니다."),
        )
        await graph.run(
            _state(message_id=10),
            data_session=data_session,
            ai_session=ai_session,
        )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None, "chat_search_queries INSERT 호출 없음"
        assert len(query_rows) == 1
        assert query_rows[0]["channel"] == SearchChannel.SQL
        assert query_rows[0]["kind"] == SearchKind.SQL
        assert query_rows[0]["message_id"] == 10

    async def test_sql_intent_inserts_result_rows_per_hit(self):
        """SQL_SEARCH + 결과 있음 → chat_search_results 에 결과 행 INSERT."""
        rows = [
            {"service_id": "SVC001", "service_name": "수영장"},
            {"service_id": "SVC002", "service_name": "헬스장"},
        ]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        await graph.run(
            _state(message_id=11),
            data_session=data_session,
            ai_session=ai_session,
        )

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None, "chat_search_results INSERT 호출 없음"
        assert len(result_rows) == 2
        service_ids = {r["service_id"] for r in result_rows}
        assert service_ids == {"SVC001", "SVC002"}
        # kind / channel 일관성
        assert all(r["kind"] == SearchKind.SQL for r in result_rows)
        assert all(r["channel"] == SearchChannel.SQL for r in result_rows)

    async def test_sql_intent_keyword_in_query_text(self):
        """sql_agent 가 keyword 를 추출하면 query_text 에 반영된다."""
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows, keyword="수영장")
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

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        assert query_rows[0]["query_text"] == "수영장"

    async def test_sql_intent_rank_is_1_based(self):
        """results 행의 rank 는 1-based 로 설정된다."""
        rows = [
            {"service_id": "A", "service_name": "A"},
            {"service_id": "B", "service_name": "B"},
            {"service_id": "C", "service_name": "C"},
        ]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        await graph.run(_state(), data_session=data_session, ai_session=ai_session)

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        ranks = {r["rank"] for r in result_rows}
        assert ranks == {1, 2, 3}


# ---------------------------------------------------------------------------
# 2. 0건 결과 — query 행은 항상 기록
# ---------------------------------------------------------------------------


class TestZeroHitQueryRecorded:
    async def test_sql_zero_results_writes_query_row(self):
        """SQL 0건 결과여도 chat_search_queries 에 sql 행이 기록된다."""
        sql_agent, data_session = _sql_agent([])
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("결과가 없습니다."),
        )
        await graph.run(_state(message_id=20), data_session=data_session, ai_session=ai_session)

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        assert len(query_rows) == 1
        assert query_rows[0]["channel"] == SearchChannel.SQL

    async def test_sql_zero_results_no_results_rows(self):
        """SQL 0건 결과이면 chat_search_results INSERT 는 생략된다."""
        sql_agent, data_session = _sql_agent([])
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("결과가 없습니다."),
        )
        await graph.run(_state(), data_session=data_session, ai_session=ai_session)

        result_rows = _get_results_rows(ai_session)
        assert result_rows is None, "hits 0건이므로 results INSERT 는 없어야 한다"


# ---------------------------------------------------------------------------
# 3. VECTOR_SEARCH intent — Phase 1 channels
# ---------------------------------------------------------------------------


class TestVectorIntentPersist:
    async def test_vector_phase1_persists_three_channels(self):
        """VECTOR_SEARCH (Phase 1) → queries: vector / bm25 / final 3행."""
        vector_rows = [{"service_id": "V001", "similarity": 0.92}]
        bm25_rows = [{"service_id": "V001", "bm25_score": 1.5}]
        hydrated = [{"service_id": "V001", "service_name": "체험관", "rrf_score": 0.05}]

        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=bm25_rows)),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent("아이랑 체험할 수 있는 곳"),
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            await graph.run(
                _state(message="아이랑 체험할 수 있는 곳", message_id=30),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None, "chat_search_queries INSERT 호출 없음"
        channels_in_queries = {r["channel"] for r in query_rows}
        assert SearchChannel.VECTOR in channels_in_queries
        assert SearchChannel.BM25 in channels_in_queries
        assert SearchChannel.FINAL in channels_in_queries
        assert len(query_rows) == 3

    async def test_vector_phase1_query_text_is_refined_query(self):
        """vector 채널의 query_text 는 refined_query 다."""
        vector_rows = [{"service_id": "V001", "similarity": 0.9}]
        hydrated = [{"service_id": "V001", "rrf_score": 0.05}]
        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent("수영장 찾기"),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(message="수영장"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        by_channel = {r["channel"]: r for r in query_rows}
        assert by_channel[SearchChannel.VECTOR]["query_text"] == "수영장 찾기"

    async def test_vector_phase1_final_query_text_is_none(self):
        """final 채널의 query_text 는 None(집계 채널이므로 원본 텍스트 없음)."""
        vector_rows = [{"service_id": "V001", "similarity": 0.9}]
        hydrated = [{"service_id": "V001", "rrf_score": 0.05}]
        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent(),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(message="수영장"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        by_channel = {r["channel"]: r for r in query_rows}
        assert by_channel[SearchChannel.FINAL]["query_text"] is None

    async def test_vector_phase1_kind_values(self):
        """vector/bm25/final 채널의 kind 가 각각 올바른 SearchKind 다."""
        vector_rows = [{"service_id": "V001", "similarity": 0.9}]
        bm25_rows = [{"service_id": "V001", "bm25_score": 1.0}]
        hydrated = [{"service_id": "V001", "rrf_score": 0.05}]
        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=bm25_rows)),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent(),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(message="체험"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        by_channel = {r["channel"]: r for r in query_rows}
        assert by_channel[SearchChannel.VECTOR]["kind"] == SearchKind.VECTOR
        assert by_channel[SearchChannel.BM25]["kind"] == SearchKind.BM25
        assert by_channel[SearchChannel.FINAL]["kind"] == SearchKind.FINAL

    async def test_vector_phase1_results_contain_hydrated_service_ids(self):
        """final 채널의 results 행은 hydrated 결과의 service_id 를 포함한다."""
        vector_rows = [{"service_id": "V001", "similarity": 0.9}]
        hydrated = [
            {"service_id": "V001", "service_name": "체험관", "rrf_score": 0.05},
            {"service_id": "V002", "service_name": "수영장", "rrf_score": 0.03},
        ]
        ai_session = _ai_session()

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            graph = AgentGraph(
                router=_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent(),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(message="체험"),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        final_results = [r for r in result_rows if r["channel"] == SearchChannel.FINAL]
        service_ids_in_final = {r["service_id"] for r in final_results}
        assert "V001" in service_ids_in_final
        assert "V002" in service_ids_in_final


# ---------------------------------------------------------------------------
# 4. MAP intent
# ---------------------------------------------------------------------------


class TestMapIntentPersist:
    async def test_map_intent_inserts_map_channel(self):
        """MAP + lat/lng 있음 → queries: map 1행."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"service_id": "M001", "distance_m": 120.0}},
            ],
        }
        ai_session = _ai_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent("주변 시설입니다."),
            )
            await graph.run(
                _state(lat=37.5665, lng=126.9780, message_id=40),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        assert len(query_rows) == 1
        row = query_rows[0]
        assert row["channel"] == SearchChannel.MAP
        assert row["kind"] == SearchKind.MAP
        assert row["message_id"] == 40

    async def test_map_intent_query_text_contains_coords(self):
        """map 채널의 query_text 에 lat/lng/radius 정보가 포함된다."""
        geojson = {"type": "FeatureCollection", "features": []}
        ai_session = _ai_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(lat=37.5, lng=126.9),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        query_text = query_rows[0]["query_text"] or ""
        assert "lat=" in query_text
        assert "lng=" in query_text

    async def test_map_intent_result_rows_per_feature(self):
        """map_search 결과 feature 수만큼 results 행이 INSERT 된다."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"service_id": "M001", "distance_m": 100.0}},
                {"type": "Feature", "properties": {"service_id": "M002", "distance_m": 200.0}},
            ],
        }
        ai_session = _ai_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            graph = AgentGraph(
                router=_router(IntentType.MAP),
                answer_agent=_answer_agent(),
            )
            await graph.run(
                _state(lat=37.5, lng=126.9),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        assert len(result_rows) == 2
        service_ids = {r["service_id"] for r in result_rows}
        assert service_ids == {"M001", "M002"}

    async def test_map_intent_no_coords_does_not_persist(self):
        """MAP + lat/lng 없음 → search_channels 미채움 → search_persist skip."""
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.MAP),
            answer_agent=_answer_agent("위치 정보가 없습니다."),
        )
        await graph.run(
            _state(lat=None, lng=None),
            data_session=MagicMock(),
            ai_session=ai_session,
        )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is None, "lat/lng 없으면 map 채널 INSERT 없어야 한다"


# ---------------------------------------------------------------------------
# 5. FALLBACK intent — 검색 없음 → 0행
# ---------------------------------------------------------------------------


class TestFallbackIntentPersist:
    async def test_fallback_intent_persists_nothing(self):
        """FALLBACK → search_channels 미채움 → 두 테이블 모두 INSERT 없음."""
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("안내 메시지입니다."),
        )
        await graph.run(
            _state(message="안녕하세요"),
            data_session=MagicMock(),
            ai_session=ai_session,
        )

        query_rows = _get_queries_rows(ai_session)
        result_rows = _get_results_rows(ai_session)
        assert query_rows is None, "FALLBACK 은 queries INSERT 없어야 한다"
        assert result_rows is None, "FALLBACK 은 results INSERT 없어야 한다"

    async def test_fallback_trace_still_saved(self):
        """FALLBACK 시 search_persist skip 이어도 trace_node 는 호출된다."""
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        await graph.run(
            _state(message="안녕하세요"),
            data_session=MagicMock(),
            ai_session=ai_session,
        )

        assert _has_trace_insert(ai_session), "FALLBACK 이어도 trace INSERT 는 있어야 한다"


# ---------------------------------------------------------------------------
# 6. self-correction 재시도 — 마지막 시도만 적재
# ---------------------------------------------------------------------------


class TestSelfCorrectionPersistOnlyLastAttempt:
    async def test_retry_resets_channels_before_second_attempt(self):
        """1회 재시도 시 retry_prep_node 가 search_channels 를 리셋하므로
        search_persist_node 에는 마지막 시도의 채널만 도달한다.
        queries 에 sql 행은 1행(마지막 시도 1회분)만 있어야 한다.
        """
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        # 첫 번째 호출 빈 답변 → 재시도 트리거, 두 번째 호출 정상 답변
        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(
            side_effect=[
                _AnswerOutput(answer=""),
                _AnswerOutput(answer="수영장 안내입니다."),
            ]
        )
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title="안내"))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=agent,
        )
        result = await graph.run(
            _state(message_id=50),
            data_session=data_session,
            ai_session=ai_session,
        )

        # 재시도 발생 확인
        assert result["retry_count"] == 1
        assert result["answer"] == "수영장 안내입니다."

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        # 두 번의 sql 실행이 있었지만 retry_prep 이 channels 를 리셋하므로
        # search_persist 에는 마지막 시도 1행만 도달한다.
        sql_rows_only = [r for r in query_rows if r["channel"] == SearchChannel.SQL]
        assert len(sql_rows_only) == 1, (
            f"재시도 후 sql 채널은 1행이어야 하지만 {len(sql_rows_only)}행임"
        )

    async def test_retry_result_has_final_attempts_service_ids(self):
        """마지막 시도의 results 행만 INSERT 되어야 한다."""
        # 두 번 모두 같은 데이터 반환 (단순화)
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(
            side_effect=[
                _AnswerOutput(answer=""),
                _AnswerOutput(answer="안내입니다."),
            ]
        )
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title="안내"))
        agent._title_chain = title_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=agent,
        )
        await graph.run(
            _state(message_id=51),
            data_session=data_session,
            ai_session=ai_session,
        )

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        # 마지막 시도 결과만 → SVC001 1행
        service_ids = [r["service_id"] for r in result_rows if r["channel"] == SearchChannel.SQL]
        assert len(service_ids) == 1
        assert service_ids[0] == "SVC001"


# ---------------------------------------------------------------------------
# 7. search_persist 실패 → trace_node 는 계속 호출
# ---------------------------------------------------------------------------


class TestPersistFailureDoesNotBlockTrace:
    async def test_persist_execute_failure_does_not_block_trace(self):
        """search_persist_node 의 execute 실패해도 trace_node 가 호출되어
        최종 result 에 trace 필드가 채워진다.
        """
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        call_count = 0
        original_execute = AsyncMock(return_value=MagicMock())

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            sql_text = str(args[0]) if args else ""
            # chat_search_queries INSERT 시 실패 유발
            if "chat_search_queries" in sql_text:
                raise RuntimeError("DB 오류 — persist 실패")
            return await original_execute(*args, **kwargs)

        ai_session.execute = AsyncMock(side_effect=execute_side_effect)

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내입니다."),
        )
        result = await graph.run(
            _state(message_id=60),
            data_session=data_session,
            ai_session=ai_session,
        )

        # 그래프가 정상 완료 (예외 미전파)
        assert result["answer"] == "수영장 안내입니다."
        # trace_node 는 실행됨 — trace 필드가 있어야 한다
        assert result.get("trace") is not None

    async def test_persist_failure_answer_still_returned(self):
        """search_persist 실패 시 answer 가 정상 반환된다."""
        sql_agent, data_session = _sql_agent([])
        ai_session = _ai_session()
        ai_session.execute = AsyncMock(side_effect=RuntimeError("모든 execute 실패"))

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        result = await graph.run(
            _state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        assert result["answer"] == "안내입니다."


# ---------------------------------------------------------------------------
# 8. kind / channel 일관성 — 양쪽 테이블에서 동일 채널의 kind 가 일치
# ---------------------------------------------------------------------------


class TestKindChannelConsistency:
    async def test_kind_consistent_between_queries_and_results(self):
        """같은 채널의 chat_search_queries.kind == chat_search_results.kind."""
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        await graph.run(_state(), data_session=data_session, ai_session=ai_session)

        query_rows = _get_queries_rows(ai_session)
        result_rows = _get_results_rows(ai_session)
        assert query_rows is not None
        assert result_rows is not None

        query_kind_by_channel = {r["channel"]: r["kind"] for r in query_rows}
        for result_row in result_rows:
            channel = result_row["channel"]
            expected_kind = query_kind_by_channel.get(channel)
            assert expected_kind is not None, f"결과 행의 채널 {channel!r} 이 쿼리 행에 없음"
            assert result_row["kind"] == expected_kind, (
                f"채널 {channel!r}: queries.kind={expected_kind!r} vs "
                f"results.kind={result_row['kind']!r} 불일치"
            )

    async def test_message_id_consistent_across_all_persist_rows(self):
        """queries / results 의 모든 행에서 message_id 가 동일하다."""
        rows = [
            {"service_id": "A", "service_name": "A"},
            {"service_id": "B", "service_name": "B"},
        ]
        sql_agent, data_session = _sql_agent(rows)
        ai_session = _ai_session()

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        await graph.run(
            _state(message_id=999),
            data_session=data_session,
            ai_session=ai_session,
        )

        query_rows = _get_queries_rows(ai_session)
        result_rows = _get_results_rows(ai_session)
        assert query_rows is not None
        assert result_rows is not None
        assert all(r["message_id"] == 999 for r in query_rows)
        assert all(r["message_id"] == 999 for r in result_rows)
