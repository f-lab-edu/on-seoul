"""hydrated_services 슬롯 통합 검증 테스트.

다음 경로를 커버한다:
1. GraphNodes.hydration_node 예외 핸들러 → {"hydrated_services": []} fallback
2. retry_prep_node 가 hydrated_services 를 None 으로 리셋
3. CacheCheckNode hit envelope 에서 hydrated_services 복원
4. CacheWriteNode 스냅에 hydrated_services 포함 여부
5. AnswerAgent._collect_results — hydrated_services 우선 로직
   (hydrated_services 있으면 sql/vector_results 무시)
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent
from agents.graph import AgentGraph
from agents.nodes import CacheCheckNode, CacheWriteNode
from agents.nodes.retrieval import RetrievalNodes
from schemas.state import AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_router,
    make_sql_agent,
    patch_node_sessions,
)


class _FakeOnDataReader:
    """B3-1: RetrievalNodes 에 주입하는 가짜 게이트웨이 — patch 불필요.

    session() 은 더미 세션을 yield 한다(hydration 콜러블은 주입된 hydration 이 처리).
    """

    @asynccontextmanager
    async def session(self):
        yield MagicMock()


def _make_retrieval(*, hydration) -> RetrievalNodes:
    """가짜 OnDataReader 를 주입한 RetrievalNodes 를 생성한다."""
    return RetrievalNodes(
        sql=MagicMock(),
        vector=MagicMock(),
        analytics=MagicMock(),
        hydration=hydration,
        ondata=_FakeOnDataReader(),
    )


# ---------------------------------------------------------------------------
# 1. GraphNodes.hydration_node 예외 핸들러
# ---------------------------------------------------------------------------


class TestHydrationNodeExceptionHandler:
    """GraphNodes.hydration_node 의 except 블록(nodes.py:284-287) 검증."""

    async def test_exception_in_hydration_call_returns_empty_fallback(self):
        """HydrationNode.__call__ 이 예외를 던지면 hydrated_services=[] 로 fallback."""
        # AsyncMock 자체가 callable 이므로 _hydration 으로 직접 사용한다.
        bad_hydration = AsyncMock(side_effect=RuntimeError("DB 연결 실패"))

        _, data_session = make_sql_agent([])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=make_sql_agent([{"service_id": "S1"}])[0],
            answer_agent=make_answer_agent("답변"),
        )
        # B3-1: hydration 은 RetrievalNodes 가 보유하므로 거기에 주입한다
        # (facade _hydration 전파 property 퇴역). 예외 던지는 callable 로 교체.
        graph._nodes._retrieval._hydration = bad_hydration

        with patch_node_sessions(
            data_session=data_session, ai_session=make_ai_session()
        ):
            result = await graph.run(make_agent_state(intent=IntentType.SQL_SEARCH))

        # hydration 예외에도 불구하고 그래프가 정상 종료되어야 한다
        assert result["output"]["answer"] == "답변"
        # hydration_error node_path 기록 확인
        assert "hydration_error" in result["node_path"]

    async def test_hydration_node_error_path_sets_empty_hydrated_services(self):
        """RetrievalNodes.hydration_node 예외 핸들러가 hydrated_services=[] 를 반환한다.

        B3-1: 가짜 OnDataReader 를 RetrievalNodes 에 주입해 patch 없이 격리한다
        (data_session_ctx 패치 불필요 — reader.session() 이 더미 세션을 yield).
        """
        bad_hydration = AsyncMock(side_effect=ValueError("예외"))

        nodes = _make_retrieval(hydration=bad_hydration)
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            sql_results=[{"service_id": "S1"}],
        )

        result = await nodes.hydration_node(state)

        assert result["hydration"]["hydrated_services"] == []
        assert "hydration_error" in result["node_path"]


# ---------------------------------------------------------------------------
# 2. retry_prep_node — hydrated_services 리셋
# ---------------------------------------------------------------------------


class TestRetryPrepNodeResetsHydratedServices:
    """retry_prep_node 가 hydrated_services 를 None 으로 리셋하는지 검증."""

    async def test_hydrated_services_reset_to_none(self):
        graph = AgentGraph(answer_agent=make_answer_agent())

        stale_state = make_agent_state(
            retry_count=0,
            sql_results=[{"service_id": "S1"}],
            hydrated_services=[{"service_id": "S1", "service_name": "이전 시설"}],
        )

        result = await graph._nodes.retry_prep_node(stale_state)

        # 그룹 통째 리셋({}) — 다음 순회 재실행 시 재-hydrate / 재검색.
        assert result["hydration"] == {}
        assert result["sql"] == {}
        assert result["vector"] == {}
        assert result["retry_count"] == 1


# ---------------------------------------------------------------------------
# 3. CacheCheckNode — hydrated_services envelope 복원
# ---------------------------------------------------------------------------


class TestCacheCheckNodeHydratedServicesRestore:
    """cache hit envelope 에서 hydrated_services 가 state 로 복원되어야 한다."""

    def _base_state(self) -> AgentState:
        return make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="서울 테니스장",
        )

    async def test_hit_envelope_restores_hydrated_services(self):
        """cache hit 시 snap.hydrated_services 가 state 로 복원된다."""
        hydrated = [{"service_id": "S1", "service_name": "테니스장"}]
        envelope = {
            "payload": {"answer": "캐시 답변", "title": None, "message_id": 1},
            "state": {
                "refined_query": "서울 테니스장",
                "vector_results": [{"service_id": "S1"}],
                "sql_results": None,
                "hydrated_services": hydrated,
                "max_class_name": None,
                "area_name": None,
                "service_status": None,
            },
        }
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(self._base_state())

        assert result["cache_hit"] is True
        assert result["hydration"]["hydrated_services"] == hydrated

    async def test_hit_envelope_without_hydrated_services_returns_none(self):
        """구버전 cache envelope(hydrated_services 미포함) — None 으로 복원된다."""
        envelope = {
            "payload": {"answer": "구버전 캐시", "title": None, "message_id": 1},
            "state": {
                "refined_query": "서울 테니스장",
                "vector_results": [{"service_id": "S1"}],
                "sql_results": None,
                # hydrated_services 키 없음 — 구버전 envelope
            },
        }
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(self._base_state())

        assert result["cache_hit"] is True
        assert (
            result["hydration"]["hydrated_services"] is None
        )  # snap.get("hydrated_services") → None


# ---------------------------------------------------------------------------
# 4. CacheWriteNode — hydrated_services 스냅 포함
# ---------------------------------------------------------------------------


class TestCacheWriteNodeHydratedServicesSnap:
    """set_cached_answer 에 snap["hydrated_services"] 가 전달되어야 한다."""

    async def test_snap_includes_hydrated_services(self):
        hydrated = [{"service_id": "S1", "service_name": "수영장"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="서울 수영장",
            answer="수영장 안내",
            vector_results=[{"service_id": "S1"}],
            hydrated_services=hydrated,
        )

        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(state)

        mock_set.assert_called_once()
        # set_cached_answer(refined, payload, snap, redis, ...)
        snap = mock_set.call_args.args[2]
        assert snap["hydrated_services"] == hydrated

    async def test_snap_hydrated_services_none_when_not_set(self):
        """hydrated_services 없이도 정상 write, snap 에 None 으로 포함."""
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            refined_query="서울 수영장",
            answer="수영장 안내",
            vector_results=[{"service_id": "S1"}],
        )
        # hydrated_services 키 자체가 없는 경우 state.get() → None

        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(state)

        mock_set.assert_called_once()
        snap = mock_set.call_args.args[2]
        assert snap["hydrated_services"] is None


# ---------------------------------------------------------------------------
# 5. AnswerAgent._collect_results — hydrated_services 우선 로직
# ---------------------------------------------------------------------------


class TestCollectResultsHydratedServicesPriority:
    """hydrated_services 가 있으면 sql/vector_results 는 무시된다."""

    def _make_agent(self, answer_text: str = "답변") -> AnswerAgent:
        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(return_value=answer_text)
        agent._answer_chain = answer_chain
        return agent

    def test_hydrated_services_takes_priority_over_sql_results(self):
        """hydrated_services 있으면 sql_results 는 collect 에서 제외된다."""
        hydrated = [{"service_id": "H1", "service_name": "hydrated시설"}]
        sql = [{"service_id": "SQL1", "service_name": "SQL시설"}]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=hydrated,
            sql_results=sql,
        )
        agent = self._make_agent()
        results = agent._collect_results(state)

        service_ids = [r["service_id"] for r in results]
        assert "H1" in service_ids
        assert "SQL1" not in service_ids

    def test_hydrated_services_takes_priority_over_vector_results(self):
        """hydrated_services 있으면 vector_results 는 collect 에서 제외된다."""
        hydrated = [{"service_id": "H1", "service_name": "hydrated시설"}]
        vector = [{"service_id": "V1", "service_name": "벡터시설"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            hydrated_services=hydrated,
            vector_results=vector,
        )
        agent = self._make_agent()
        results = agent._collect_results(state)

        service_ids = [r["service_id"] for r in results]
        assert "H1" in service_ids
        assert "V1" not in service_ids

    def test_empty_hydrated_services_does_not_fall_back_to_sql_results(self):
        """hydrated_services 가 빈 리스트이면 sql_results 로 폴백하지 않는다.

        HydrationNode 가 MAP/FALLBACK 경로에서 의도적으로 [] 를 채운 경우,
        빈 리스트가 falsy 여서 sql_results 로 폴백하는 것은 버그다.
        `if hydrated is not None` 조건으로 빈 리스트도 정상 경로로 처리한다.
        """
        sql = [{"service_id": "SQL1", "service_name": "SQL시설"}]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],  # not None → 폴백 없이 빈 결과 반환
            sql_results=sql,
        )
        agent = self._make_agent()
        results = agent._collect_results(state)

        service_ids = [r["service_id"] for r in results]
        assert "SQL1" not in service_ids  # 폴백 없이 hydrated_services([]) 가 사용됨
        assert results == []

    def test_none_hydrated_services_falls_back_to_vector_results(self):
        """hydrated_services=None 이면 vector_results 로 폴백한다."""
        vector = [{"service_id": "V1", "service_name": "벡터시설"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            hydrated_services=None,
            vector_results=vector,
        )
        agent = self._make_agent()
        results = agent._collect_results(state)

        service_ids = [r["service_id"] for r in results]
        assert "V1" in service_ids

    async def test_answer_uses_hydrated_services_data_for_llm_context(self):
        """answer() 가 hydrated_services 데이터를 LLM 컨텍스트에 전달한다."""
        hydrated = [
            {
                "service_id": "H1",
                "service_name": "hydrated시설",
                "service_url": "https://example.com/h1",
            }
        ]
        sql = [{"service_id": "SQL1", "service_name": "SQL시설", "service_url": None}]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            message="시설 알려줘",
            hydrated_services=hydrated,
            sql_results=sql,
        )
        agent = self._make_agent("답변")
        await agent.answer(state)

        results_json = agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        assert "hydrated시설" in results_json
        assert "SQL시설" not in results_json


# ---------------------------------------------------------------------------
# 6. make_agent_state — hydrated_services 누락 회귀
# ---------------------------------------------------------------------------


class TestMakeAgentStateHelperHydratedServices:
    """helpers.make_agent_state 가 hydrated_services 키를 포함하지 않는 회귀 탐지."""

    def test_make_agent_state_includes_hydrated_services(self):
        """make_agent_state 가 hydrated_services=None 을 기본값으로 포함한다."""
        from schemas.state import AgentState

        state = make_agent_state()
        schema_keys = set(AgentState.__annotations__.keys())
        missing = schema_keys - set(state.keys())
        assert missing == set(), f"make_agent_state 에서 누락된 키: {missing}"
        assert state["hydration"].get("hydrated_services") is None
