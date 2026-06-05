"""그래프 end-to-end — intent별 search_channels 적재 smoke 테스트.

AgentGraph.run() 을 실제로 실행하여 search_persist_node 가 의도한 채널 데이터를
ai_session 에 전달하는지 intent별 대표 시나리오로 검증한다.

세부 persist 계약(kind 일관성, message_id 전파, rank 1-based 등)은
test_search_persist_node.py 의 단위 테스트에서 커버한다.
이 파일은 "full-graph → persist까지 파이프라인이 연결됐는지"만 확인한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent, _TitleOutput
from agents.graph import AgentGraph
from schemas.search import SearchChannel
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_router,
    make_sql_agent,
)
from agents.vector_agent import VectorAgent, _RefinedQuery


# ---------------------------------------------------------------------------
# 헬퍼 — 이 파일 전용
# ---------------------------------------------------------------------------


def _state(**kwargs):
    return make_agent_state(**kwargs)


def _vector_agent(refined_query: str = "정제된 질의") -> VectorAgent:
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


def _get_queries_rows(ai_session: MagicMock) -> list[dict] | None:
    """ai_session.execute 호출 중 chat_search_queries INSERT 에 전달된 rows."""
    for call in ai_session.execute.call_args_list:
        if "chat_search_queries" in str(call.args[0]):
            return call.args[1]
    return None


def _get_results_rows(ai_session: MagicMock) -> list[dict] | None:
    """ai_session.execute 호출 중 chat_search_results INSERT 에 전달된 rows."""
    for call in ai_session.execute.call_args_list:
        if "chat_search_results" in str(call.args[0]):
            return call.args[1]
    return None


def _has_trace_insert(ai_session: MagicMock) -> bool:
    return any(
        "chat_agent_traces" in str(call.args[0])
        for call in ai_session.execute.call_args_list
    )


# ---------------------------------------------------------------------------
# 1. SQL_SEARCH — sql 채널 적재
# ---------------------------------------------------------------------------


class TestSqlIntentPersist:
    async def test_sql_intent_inserts_sql_channel_query(self):
        """SQL_SEARCH → chat_search_queries 에 sql 채널 1행, results 에 결과 행 INSERT."""
        rows = [
            {"service_id": "SVC001", "service_name": "수영장"},
            {"service_id": "SVC002", "service_name": "헬스장"},
        ]
        sql_agent, data_session = make_sql_agent(rows)
        ai_session = make_ai_session()

        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("수영장 안내입니다."),
        )
        await graph.run(
            _state(message_id=10),
            data_session=data_session,
            ai_session=ai_session,
        )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None, "chat_search_queries INSERT 없음"
        assert len(query_rows) == 1
        assert query_rows[0]["channel"] == SearchChannel.SQL
        assert query_rows[0]["message_id"] == 10

        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        assert {r["service_id"] for r in result_rows} == {"SVC001", "SVC002"}


# ---------------------------------------------------------------------------
# 2. 0건 결과 — query 행은 항상 기록
# ---------------------------------------------------------------------------


class TestZeroHitQueryRecorded:
    async def test_sql_zero_results_writes_query_row_only(self):
        """SQL 0건이어도 chat_search_queries 에 sql 행이 기록되고 results 는 생략된다."""
        sql_agent, data_session = make_sql_agent([])
        ai_session = make_ai_session()

        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("결과가 없습니다."),
        )
        await graph.run(
            _state(message_id=20), data_session=data_session, ai_session=ai_session
        )

        assert _get_queries_rows(ai_session) is not None
        assert _get_results_rows(ai_session) is None, (
            "0건이면 results INSERT 없어야 한다"
        )


# ---------------------------------------------------------------------------
# 3. VECTOR_SEARCH — vector / bm25 / final 3채널 적재
# ---------------------------------------------------------------------------


class TestVectorIntentPersist:
    async def test_vector_phase1_persists_three_channels(self):
        """VECTOR_SEARCH → queries: vector_a / vector_b / vector_c / bm25 / rrf / final 6행 INSERT."""
        vector_rows = [
            {
                "service_id": "V001",
                "embedding_text": "t",
                "metadata": {},
                "similarity": 0.92,
            }
        ]
        bm25_rows = [{"service_id": "V001", "bm25_score": 1.5}]
        hydrated = [{"service_id": "V001", "service_name": "체험관", "rrf_score": 0.05}]
        ai_session = make_ai_session()

        with (
            patch(
                "agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)
            ),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=bm25_rows)),
            patch(
                "agents.hydration_node.hydrate_services", AsyncMock(return_value=hydrated)
            ),
        ):
            graph = AgentGraph(
                router=make_router(IntentType.VECTOR_SEARCH),
                vector_agent=_vector_agent("아이랑 체험할 수 있는 곳"),
                answer_agent=make_answer_agent("체험관 안내입니다."),
            )
            await graph.run(
                _state(message="아이랑 체험할 수 있는 곳", message_id=30),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None, "chat_search_queries INSERT 없음"
        channels = {r["channel"] for r in query_rows}
        # Phase RRF: 5채널 (vector_a, vector_b, vector_c, bm25, rrf)
        # FINAL 채널은 VectorAgent 가 더 이상 구성하지 않는다 (HydrationNode 책임)
        expected = {
            SearchChannel.VECTOR_A,
            SearchChannel.VECTOR_B,
            SearchChannel.VECTOR_C,
            SearchChannel.BM25,
            SearchChannel.RRF,
        }
        assert expected == channels


# ---------------------------------------------------------------------------
# 4. MAP — map 채널 적재
# ---------------------------------------------------------------------------


class TestMapIntentPersist:
    async def test_map_intent_inserts_map_channel(self):
        """MAP + lat/lng → queries: map 1행, results: feature 수만큼 INSERT."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"service_id": "M001", "distance_m": 120.0},
                },
                {
                    "type": "Feature",
                    "properties": {"service_id": "M002", "distance_m": 200.0},
                },
            ],
        }
        ai_session = make_ai_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            graph = AgentGraph(
                router=make_router(IntentType.MAP),
                answer_agent=make_answer_agent("주변 시설입니다."),
            )
            await graph.run(
                _state(user_lat=37.5665, user_lng=126.9780, message_id=40),
                data_session=MagicMock(),
                ai_session=ai_session,
            )

        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        assert query_rows[0]["channel"] == SearchChannel.MAP
        result_rows = _get_results_rows(ai_session)
        assert result_rows is not None
        assert len(result_rows) == 2


# ---------------------------------------------------------------------------
# 5. FALLBACK — 검색 없음 → 0행
# ---------------------------------------------------------------------------


class TestFallbackIntentPersist:
    async def test_fallback_intent_persists_nothing_but_trace(self):
        """FALLBACK → queries/results INSERT 없음, trace 는 기록됨."""
        ai_session = make_ai_session()

        graph = AgentGraph(
            router=make_router(IntentType.FALLBACK),
            answer_agent=make_answer_agent("안내 메시지입니다."),
        )
        await graph.run(
            _state(message="안녕하세요"),
            data_session=MagicMock(),
            ai_session=ai_session,
        )

        assert _get_queries_rows(ai_session) is None
        assert _get_results_rows(ai_session) is None
        assert _has_trace_insert(ai_session), (
            "FALLBACK 이어도 trace INSERT 는 있어야 한다"
        )


# ---------------------------------------------------------------------------
# 6. self-correction 재시도 — 마지막 시도만 적재
# ---------------------------------------------------------------------------


class TestSelfCorrectionPersistOnlyLastAttempt:
    async def test_retry_resets_channels_before_second_attempt(self):
        """재시도(SQL 빈 답변→VECTOR 전환) 시 retry_prep_node 가 channels 를 리셋한다.

        방향성 재시도: SQL_SEARCH 의 재시도는 VECTOR_SEARCH 로 강제 전환된다.
        1차 SQL 채널은 리셋되고 마지막 시도(VECTOR) 채널만 적재되어 SQL 채널은 0행이다.
        """
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        ai_session = make_ai_session()
        vector_agent = _vector_agent()

        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(
            side_effect=[
                "",  # 1차 SQL — 빈 답변 → 재시도 트리거
                "체험관 안내입니다.",  # 2차 VECTOR 전환
            ]
        )
        agent._answer_chain = answer_chain
        title_chain = MagicMock()
        title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title="안내"))
        agent._title_chain = title_chain

        vrows = [{"service_id": "VEC001", "service_name": "체험관", "similarity": 0.9}]
        hydrated = [{"service_id": "VEC001", "service_name": "체험관"}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                router=make_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=agent,
            )
            result = await graph.run(
                _state(message_id=50),
                data_session=data_session,
                ai_session=ai_session,
            )

        assert result["retry_count"] == 1
        query_rows = _get_queries_rows(ai_session)
        assert query_rows is not None
        sql_rows = [r for r in query_rows if r["channel"] == SearchChannel.SQL]
        # 1차 SQL 채널은 리셋됨 — 마지막 시도는 VECTOR 전환이므로 SQL 0행.
        assert len(sql_rows) == 0, f"전환 후 SQL 채널은 0행이어야 하지만 {len(sql_rows)}행"


# ---------------------------------------------------------------------------
# 7. search_persist 실패 → trace_node 는 계속 호출
# ---------------------------------------------------------------------------


class TestPersistFailureDoesNotBlockTrace:
    async def test_persist_failure_does_not_block_trace(self):
        """search_persist_node 실패해도 trace_node 가 실행되고 answer 도 정상 반환."""
        rows = [{"service_id": "SVC001", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        ai_session = make_ai_session()

        original_execute = AsyncMock(return_value=MagicMock())

        async def execute_side_effect(*args, **kwargs):
            if "chat_search_queries" in str(args[0] if args else ""):
                raise RuntimeError("DB 오류")
            return await original_execute(*args, **kwargs)

        ai_session.execute = AsyncMock(side_effect=execute_side_effect)

        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("수영장 안내입니다."),
        )
        result = await graph.run(
            _state(message_id=60),
            data_session=data_session,
            ai_session=ai_session,
        )

        assert result["answer"] == "수영장 안내입니다."
        assert result.get("trace") is not None
