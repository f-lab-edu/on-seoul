"""cache_check / cache_write 노드 및 graph 라우팅 통합 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.state import AgentState, IntentType


@pytest.fixture
def base_state() -> AgentState:
    """캐시 노드 단위 테스트용 base state.

    refined_query를 미리 채운 상태를 시뮬레이션한다. 실서비스 흐름에서
    이 값은 Router가 _IntentOutput.refined_query로 산출하여 router_node가
    state["refined_query"]에 기록한다. Router가 산출하지 않은 경우
    (refined_query=None) cache_check_node는 자연스럽게 lookup을 건너뛴다.
    """
    return AgentState(
        room_id=1,
        message_id=1,
        message="테니스장",
        title_needed=True,
        intent=None,
        user_lat=None,
        user_lng=None,
        refined_query="서울 테니스장",
        max_class_name=None,
        area_name=None,
        service_status=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
        retry_count=0,
        recent_queries=[],
        cache_hit=False,
    )


# ---------------------------------------------------------------------------
# CacheCheckNode 단위 테스트
# ---------------------------------------------------------------------------


class TestCacheCheckNode:
    async def test_eligible_hit_populates_state(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        envelope = {
            "payload": {
                "answer": "캐시 답변",
                "intent": "VECTOR_SEARCH",
                "title": None,
                "message_id": 1,
            },
            "state": {
                "refined_query": "서울 테니스장",
                "max_class_name": "체육시설",
                "area_name": "강남구",
                "service_status": "접수중",
                "vector_results": [{"service_id": "S1"}],
                "sql_results": None,
            },
        }
        with patch(
            "agents.nodes.get_cached_answer",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is True
        assert result["answer"] == "캐시 답변"
        assert result["vector_results"] == [{"service_id": "S1"}]
        assert result["sql_results"] is None
        # post-filter snapshot은 cache hit 시 state로 복원되어야 한다
        assert result["max_class_name"] == "체육시설"
        assert result["area_name"] == "강남구"
        assert result["service_status"] == "접수중"

    async def test_eligible_miss_passes_through(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        with patch(
            "agents.nodes.get_cached_answer",
            AsyncMock(return_value=None),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is False
        assert "answer" not in result

    async def test_non_eligible_intent_skips_lookup(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["intent"] = IntentType.MAP
        with patch("agents.nodes.get_cached_answer", AsyncMock()) as mock_get:
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        mock_get.assert_not_called()
        assert result["cache_hit"] is False

    async def test_same_query_different_area_produces_cache_miss(self, base_state):
        """동일 refined_query라도 area_name이 다르면 다른 키 → cache miss.

        Router LLM이 prompt 위반으로 메타데이터를 분리 산출하더라도
        사용자 간 잘못된 cache hit이 발생하지 않음을 보장한다.
        """
        from core.cache import _cache_key

        # 기존 사용자가 area_name=강남구로 캐싱했다고 가정
        cached_key = _cache_key("서울 테니스장", area_name="강남구")

        # fake redis: cached_key 에만 envelope를 보관
        envelope_raw = '{"payload": {"answer": "강남 답변"}, "state": {"refined_query": "서울 테니스장"}}'

        fake_redis = AsyncMock()

        async def _get(key):
            if key == cached_key:
                return envelope_raw
            return None

        fake_redis.get.side_effect = _get

        # 새 사용자: 같은 refined_query, 다른 area_name → miss여야 한다
        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["refined_query"] = "서울 테니스장"
        base_state["area_name"] = "성동구"

        from agents.nodes import CacheCheckNode

        node = CacheCheckNode(redis=fake_redis)
        result = await node(base_state)

        assert result["cache_hit"] is False

    async def test_none_refined_query_skips_lookup(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["refined_query"] = None
        with patch("agents.nodes.get_cached_answer", AsyncMock()) as mock_get:
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        mock_get.assert_not_called()
        assert result["cache_hit"] is False


# ---------------------------------------------------------------------------
# CacheWriteNode 단위 테스트
# ---------------------------------------------------------------------------


class TestCacheWriteNode:
    async def test_writes_on_success(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["answer"] = "신규 답변"
        base_state["vector_results"] = [{"service_id": "S1"}]
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_called_once()
        args = mock_set.call_args.args
        # signature: set_cached_answer(refined, payload, snap, redis)
        assert args[0] == "서울 테니스장"
        assert args[1]["answer"] == "신규 답변"
        assert args[2]["vector_results"] == [{"service_id": "S1"}]

    async def test_skips_on_error(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["answer"] = "x"
        base_state["error"] = "boom"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()

    async def test_skips_on_cache_hit(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["intent"] = IntentType.VECTOR_SEARCH
        base_state["cache_hit"] = True
        base_state["answer"] = "x"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()

    async def test_skips_non_eligible_intent(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["intent"] = IntentType.MAP
        base_state["answer"] = "x"
        with patch("agents.nodes.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()


# ---------------------------------------------------------------------------
# Graph 라우팅 통합: cache_hit=True 시 sql/vector/answer 미호출
# ---------------------------------------------------------------------------


class TestGraphRouting:
    async def test_cache_hit_routes_to_end_skipping_search_and_answer(self):
        """cache_check_node가 hit을 반환하면 sql/vector/map/answer 노드가 호출되지 않는다.

        cache_check 직후 conditional edge로 trace_node를 거쳐 END에 도달한다.
        """
        from agents.answer_agent import AnswerAgent
        from agents.graph import AgentGraph
        from agents.router_agent import RouterAgent, _IntentOutput
        from agents.sql_agent import SqlAgent
        from agents.vector_agent import VectorAgent

        # router는 VECTOR_SEARCH로 분류 (eligible intent)
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(intent=IntentType.VECTOR_SEARCH)
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        # sql / vector / answer agent — 호출되면 안 됨을 검증할 모의 객체
        sql_agent = SqlAgent.__new__(SqlAgent)
        sql_agent._chain = MagicMock()
        sql_agent._chain.ainvoke = AsyncMock()

        vector_agent = VectorAgent.__new__(VectorAgent)
        vector_agent._refine_chain = MagicMock()
        vector_agent._refine_chain.ainvoke = AsyncMock()
        vector_agent._embeddings = MagicMock()
        vector_agent._embeddings.aembed_query = AsyncMock()

        answer_agent = AnswerAgent.__new__(AnswerAgent)
        answer_agent._answer_chain = MagicMock()
        answer_agent._answer_chain.ainvoke = AsyncMock()
        answer_agent._title_chain = MagicMock()
        answer_agent._title_chain.ainvoke = AsyncMock()

        envelope = {
            "payload": {
                "answer": "캐시된 답변",
                "intent": "VECTOR_SEARCH",
                "title": "캐시 제목",
                "message_id": 1,
            },
            "state": {
                "refined_query": "서울 테니스장",
                "max_class_name": None,
                "area_name": None,
                "service_status": None,
                "vector_results": [{"service_id": "S1"}],
                "sql_results": None,
            },
        }

        data_session = MagicMock()
        data_session.execute = AsyncMock()
        ai_session = MagicMock()
        ai_session.execute = AsyncMock()
        ai_session.commit = AsyncMock()
        ai_session.rollback = AsyncMock()

        state = AgentState(
            room_id=1,
            message_id=1,
            message="테니스장",
            title_needed=False,
            intent=None,
            lat=None,
            lng=None,
            refined_query="서울 테니스장",  # cache_check가 lookup하도록 미리 채움
            max_class_name=None,
            area_name=None,
            service_status=None,
            sql_results=None,
            vector_results=None,
            map_results=None,
            answer=None,
            title=None,
            trace=None,
            error=None,
            retry_count=0,
            recent_queries=[],
            cache_hit=False,
        )

        with patch(
            "agents.nodes.get_cached_answer",
            AsyncMock(return_value=envelope),
        ):
            graph = AgentGraph(
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=answer_agent,
                redis=AsyncMock(),
            )
            result = await graph.run(
                state,
                data_session=data_session,
                ai_session=ai_session,
            )

        # cache hit envelope가 state에 복원되었다
        assert result["cache_hit"] is True
        assert result["answer"] == "캐시된 답변"
        assert result["vector_results"] == [{"service_id": "S1"}]

        # sql/vector/answer agent의 LLM 호출이 일어나지 않았다
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()
        answer_agent._answer_chain.ainvoke.assert_not_called()
