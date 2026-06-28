"""cache_check / cache_write 노드 및 graph 라우팅 통합 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from schemas.state import AgentState, IntentType
from tests.helpers import make_agent_state, run_graph


class _FakeRedis:
    """SET NX / GET / DEL을 인메모리로 모사하는 fake Redis.

    singleflight 락 누수 회귀 검증용 — 같은 키 SET NX 재요청 시 락이 풀려 있으면
    True(획득), 풀려 있지 않으면 None(실패)을 반환해 실제 stampede 동작을 재현한다.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []

    async def get(self, key):  # noqa: ANN001
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, key, value, *, nx=False, ex=None):  # noqa: ANN001
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys):  # noqa: ANN002
        n = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                n += 1
        return n


@pytest.fixture
def base_state() -> AgentState:
    """캐시 노드 단위 테스트용 base state.

    refined_query를 미리 채운 상태를 시뮬레이션한다. 실서비스 흐름에서
    이 값은 Router가 _IntentOutput.refined_query로 산출하여 router_node가
    state["plan"]["refined_query"]에 기록한다. Router가 산출하지 않은 경우
    (refined_query=None) cache_check_node는 자연스럽게 lookup을 건너뛴다.
    """
    return make_agent_state(
        message="테니스장",
        title_needed=True,
        refined_query="서울 테니스장",
    )


# ---------------------------------------------------------------------------
# CacheCheckNode 단위 테스트
# ---------------------------------------------------------------------------


class TestCacheCheckNode:
    async def test_eligible_hit_populates_state(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
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
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is True
        assert result["output"]["answer"] == "캐시 답변"
        assert result["vector"]["results"] == [{"service_id": "S1"}]
        assert result["sql"]["results"] is None
        # post-filter snapshot은 cache hit 시 state로 복원되어야 한다
        assert result["filters"]["max_class_name"] == "체육시설"
        assert result["filters"]["area_name"] == "강남구"
        assert result["filters"]["service_status"] == "접수중"

    async def test_eligible_miss_passes_through(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=None),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is False
        assert "answer" not in result

    async def test_non_eligible_intent_skips_lookup(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.MAP
        with patch("agents._redis_gateway.get_cached_answer_by_key", AsyncMock()) as mock_get:
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
        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["plan"]["refined_query"] = "서울 테니스장"
        base_state["filters"]["area_name"] = "성동구"

        from agents.nodes import CacheCheckNode

        node = CacheCheckNode(redis=fake_redis)
        result = await node(base_state)

        assert result["cache_hit"] is False

    async def test_none_refined_query_skips_lookup(self, base_state):
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["plan"]["refined_query"] = None
        with patch("agents._redis_gateway.get_cached_answer_by_key", AsyncMock()) as mock_get:
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

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["output"]["answer"] = "신규 답변"
        base_state["vector"]["results"] = [{"service_id": "S1"}]
        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
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

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["output"]["answer"] = "x"
        base_state["error"] = "boom"
        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()

    async def test_skips_on_cache_hit(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["cache_hit"] = True
        base_state["output"]["answer"] = "x"
        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()

    async def test_cache_write_includes_service_cards(self, base_state):
        """write 시 payload 에 service_cards 가 포함된다 (snap 이 아닌 payload)."""
        from agents.nodes import CacheWriteNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["output"]["answer"] = "신규 답변"
        base_state["output"]["service_cards"] = [
            {"service_id": "S1", "service_name": "수영장"},
            {"service_id": "S2", "service_name": "체육관"},
        ]
        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_called_once()
        args = mock_set.call_args.args
        payload, snap = args[1], args[2]
        assert payload["service_cards"] == [
            {"service_id": "S1", "service_name": "수영장"},
            {"service_id": "S2", "service_name": "체육관"},
        ]
        # search snapshot 이 아니므로 snap 에 들어가서는 안 된다
        assert "service_cards" not in snap

    async def test_cache_check_restores_service_cards(self, base_state):
        """hit 시 envelope payload 의 service_cards 가 state 로 복원된다."""
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        envelope = {
            "payload": {
                "answer": "캐시 답변",
                "title": None,
                "message_id": 1,
                "service_cards": [{"service_id": "S1", "service_name": "수영장"}],
            },
            "state": {"refined_query": "서울 테니스장"},
        }
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is True
        assert result["output"]["service_cards"] == [
            {"service_id": "S1", "service_name": "수영장"}
        ]

    async def test_cache_check_legacy_envelope_without_service_cards(self, base_state):
        """구버전 envelope (payload 에 service_cards 없음) → None 으로 안전 복원."""
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        envelope = {
            "payload": {"answer": "구버전 캐시", "title": None, "message_id": 1},
            "state": {"refined_query": "서울 테니스장"},
        }
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            node = CacheCheckNode(redis=AsyncMock())
            result = await node(base_state)

        assert result["cache_hit"] is True
        # 키는 존재하되 None — 라우터의 `or []` 가 빈 배열로 노출함
        assert "service_cards" in result["output"]
        assert result["output"]["service_cards"] is None

    async def test_skips_non_eligible_intent(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["plan"]["intent"] = IntentType.MAP
        base_state["output"]["answer"] = "x"
        with patch("agents._redis_gateway.set_cached_answer", AsyncMock()) as mock_set:
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

        state = make_agent_state(
            message="테니스장",
            refined_query="서울 테니스장",  # cache_check가 lookup하도록 미리 채움
        )

        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            graph = AgentGraph(
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=answer_agent,
                redis=AsyncMock(),
            )
            result = await run_graph(
                graph,
                state,
                data_session=data_session,
                ai_session=ai_session,
            )

        # cache hit envelope가 state에 복원되었다
        assert result["cache_hit"] is True
        assert result["output"]["answer"] == "캐시된 답변"
        assert result["vector"]["results"] == [{"service_id": "S1"}]

        # sql/vector/answer agent의 LLM 호출이 일어나지 않았다
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()
        answer_agent._answer_chain.ainvoke.assert_not_called()


# ---------------------------------------------------------------------------
# Singleflight 락 누수 회귀 (C2 0건 게이트 → retry_prep 우회 경로)
# ---------------------------------------------------------------------------


class TestSingleflightLockLeak:
    """C2 0건 게이트가 cache_write를 우회할 때 첫 패스 락이 해제되는지 검증.

    회귀: cache_check가 SET NX로 잡은 락이 cache_write에서만 풀렸는데,
    C2 게이트(hydrated=[] & retry_count==0)는 cache_write를 우회하고 retry_prep로
    직행한다. 재진입한 cache_check가 미해제 락을 다시 잡지 못해 poll 타임아웃을
    소진했다. retry_prep가 직전 패스 락을 해제해야 한다.
    """

    def _eligible_state(self) -> AgentState:
        state = make_agent_state(
            message="테니스장",
            refined_query="서울 테니스장",
        )
        state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        state["triage"]["action"] = None  # RETRIEVE 경로 (None == RETRIEVE 게이트 통과)
        return state

    async def test_cache_check_records_lock_key_on_acquire(self):
        from agents.nodes import CacheCheckNode
        from core.cache import _LOCK_SUFFIX

        redis = _FakeRedis()
        node = CacheCheckNode(redis=redis)
        result = await node(self._eligible_state())

        assert result["cache_hit"] is False
        key = result["answer_lock_key"]
        assert key is not None
        # 락이 실제로 SET NX로 잡혔다
        assert f"{key}{_LOCK_SUFFIX}" in redis.store

    async def test_retry_prep_releases_recorded_lock(self):
        """retry_prep_node가 answer_lock_key로 락을 DEL하고 슬롯을 비운다."""
        from agents.nodes import CacheCheckNode
        from agents.nodes.correction import CorrectionNodes
        from core.cache import _LOCK_SUFFIX

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)
        state = self._eligible_state()
        check_result = await check(state)
        lock_key = check_result["answer_lock_key"]
        lock_full = f"{lock_key}{_LOCK_SUFFIX}"
        assert lock_full in redis.store

        # retry_prep로 진입 (state에 lock key 반영)
        # B3-1: __new__ 우회 + _redis 세팅 대신 CorrectionNodes 정상 생성/주입
        # (facade _correction_phase 지연 빌더 퇴역).
        state["answer_lock_key"] = lock_key
        nodes = CorrectionNodes(redis=redis)
        retry_update = await nodes.retry_prep_node(state)

        # 첫 패스 락이 해제되고 슬롯이 비워졌다
        assert lock_full not in redis.store
        assert retry_update["answer_lock_key"] is None

    async def test_reentry_cache_check_acquires_without_poll(self):
        """retry_prep 락 해제 후 재진입 cache_check가 SET NX 성공 → poll 미진입."""
        from agents.nodes import CacheCheckNode
        from agents.nodes.correction import CorrectionNodes

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)

        # 첫 패스: miss → 락 획득
        state = self._eligible_state()
        first = await check(state)
        lock_key = first["answer_lock_key"]

        # C2 게이트 → retry_prep 가 락 해제 (B3-1: CorrectionNodes 정상 생성)
        state["answer_lock_key"] = lock_key
        nodes = CorrectionNodes(redis=redis)
        await nodes.retry_prep_node(state)

        # 재진입 cache_check (동일 키): poll을 호출하지 않고 락 재획득해야 한다
        poll_mock = AsyncMock()
        with patch("agents._redis_gateway.poll_for_answer", poll_mock):
            reentry = await check(self._eligible_state())

        poll_mock.assert_not_called()
        assert reentry["cache_hit"] is False
        assert reentry["answer_lock_key"] == lock_key

    async def test_cache_write_releases_recorded_lock(self):
        """cache_write도 answer_lock_key 기준으로 정합되게 해제한다(이중 해제 무해)."""
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from core.cache import _LOCK_SUFFIX

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)
        state = self._eligible_state()
        check_result = await check(state)
        lock_key = check_result["answer_lock_key"]
        assert f"{lock_key}{_LOCK_SUFFIX}" in redis.store

        state["answer_lock_key"] = lock_key
        state["output"]["answer"] = "신규 답변"
        state["vector"]["results"] = [{"service_id": "S1"}]
        write = CacheWriteNode(redis=redis)
        write_update = await write(state)

        assert f"{lock_key}{_LOCK_SUFFIX}" not in redis.store
        assert write_update["answer_lock_key"] is None

    async def test_singleflight_disabled_is_noop(self):
        """singleflight 비활성 시 cache_check는 락을 잡지 않고 슬롯도 비운다."""
        from agents.nodes import CacheCheckNode
        from agents.nodes.correction import CorrectionNodes
        from core.config import settings

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)
        with patch.object(settings, "answer_cache_singleflight_enabled", False):
            result = await check(self._eligible_state())
        # acquire가 no-op(True)이므로 락 키가 저장되지 않는다
        assert not any(k.endswith(":lock") for k in redis.store)
        assert result["cache_hit"] is False

        # retry_prep도 no-op release (raise 없음) — CorrectionNodes 정상 생성
        state = self._eligible_state()
        state["answer_lock_key"] = result.get("answer_lock_key")
        nodes = CorrectionNodes(redis=redis)
        with patch.object(settings, "answer_cache_singleflight_enabled", False):
            await nodes.retry_prep_node(state)  # no raise

    async def test_cache_hit_leaves_lock_key_unset(self, base_state):
        """cache hit 경로는 락을 들지 않으므로 answer_lock_key를 기록하지 않는다.

        반환 dict에 answer_lock_key 키가 없으면(LangGraph 부분 머지) 슬롯은 초기
        None 그대로 유지 → retry_prep/cache_write가 잘못된 release를 하지 않는다.
        """
        from agents.nodes import CacheCheckNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        envelope = {
            "payload": {"answer": "캐시 답변", "title": None},
            "state": {"refined_query": "서울 테니스장", "vector_results": [], "sql_results": None},
        }
        redis = _FakeRedis()
        with patch(
            "agents._redis_gateway.get_cached_answer_by_key",
            AsyncMock(return_value=envelope),
        ):
            result = await CacheCheckNode(redis=redis)(base_state)

        assert result["cache_hit"] is True
        # 락 미보유 → 슬롯을 건드리지 않는다(키 부재 == 변경 없음 == None 유지)
        assert result.get("answer_lock_key") is None
        # 락 키를 SET NX로 잡지도 않았다
        assert not any(k.endswith(":lock") for k in redis.store)

    async def test_poll_hit_leaves_lock_key_unset(self):
        """poll-hit 경로(acquire 실패 후 waiter가 결과 수신)도 락을 들지 않는다.

        acquire=False(다른 호출자 보유) → poll_for_answer가 envelope 반환 →
        hit 분기로 합류. 이 패스는 lock_key를 None으로 유지해야 한다.
        """
        from agents.nodes import CacheCheckNode

        redis = _FakeRedis()
        envelope = {
            "payload": {"answer": "다른 호출자 답변", "title": None},
            "state": {"refined_query": "서울 테니스장", "vector_results": [], "sql_results": None},
        }
        with (
            patch("agents._redis_gateway.acquire_answer_lock", AsyncMock(return_value=False)),
            patch("agents._redis_gateway.poll_for_answer", AsyncMock(return_value=envelope)),
        ):
            result = await CacheCheckNode(redis=redis)(self._eligible_state())

        assert result["cache_hit"] is True
        assert result.get("answer_lock_key") is None

    async def test_poll_timeout_leaves_lock_key_unset(self):
        """poll 타임아웃(fail-open) 경로도 락을 들지 않아 슬롯이 None으로 남는다."""
        from agents.nodes import CacheCheckNode

        redis = _FakeRedis()
        with (
            patch("agents._redis_gateway.acquire_answer_lock", AsyncMock(return_value=False)),
            patch("agents._redis_gateway.poll_for_answer", AsyncMock(return_value=None)),
        ):
            result = await CacheCheckNode(redis=redis)(self._eligible_state())

        assert result["cache_hit"] is False
        # waiter는 락 보유자가 아니므로 키를 넘기지 않는다(잘못된 release 방지)
        assert result.get("answer_lock_key") is None

    async def test_redis_failure_records_key_but_release_swallows(self):
        """Redis 장애 fail-open: acquire=True로 키 기록되나 release 예외는 삼킨다.

        acquire가 SET 예외 시 True(fail-open)를 반환 → cache_check가 lock_key를
        기록한다. 이후 retry_prep의 release가 DEL에서 예외를 던져도 메인 흐름이
        막히지 않아야 한다(release_answer_lock의 except가 삼킴).
        """
        from agents.nodes import CacheCheckNode
        from agents.nodes.correction import CorrectionNodes

        class _RaisingRedis(_FakeRedis):
            async def set(self, key, value, *, nx=False, ex=None):  # noqa: ANN001
                raise ConnectionError("redis down on set")

            async def delete(self, *keys):  # noqa: ANN002
                raise ConnectionError("redis down on del")

        redis = _RaisingRedis()
        # acquire는 fail-open(True) → 키 기록
        check_result = await CacheCheckNode(redis=redis)(self._eligible_state())
        assert check_result["cache_hit"] is False
        assert check_result["answer_lock_key"] is not None

        # retry_prep의 release가 DEL 예외를 삼켜야 한다(메인 흐름 안 막힘)
        state = self._eligible_state()
        state["answer_lock_key"] = check_result["answer_lock_key"]
        nodes = CorrectionNodes(redis=redis)
        retry_update = await nodes.retry_prep_node(state)  # no raise
        assert retry_update["answer_lock_key"] is None

    async def test_double_release_is_harmless(self):
        """이중 해제 무해성: retry_prep가 해제한 락을 cache_write가 다시 해제해도 안전.

        2회차 retry_count 캡 도달 등으로 동일 락 키에 대해 release가 두 번
        호출되어도 DEL은 멱등(이미 없는 키 → no-op)이라 예외/오류가 없다.
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from agents.nodes.correction import CorrectionNodes
        from core.cache import _LOCK_SUFFIX

        redis = _FakeRedis()
        check_result = await CacheCheckNode(redis=redis)(self._eligible_state())
        lock_key = check_result["answer_lock_key"]
        lock_full = f"{lock_key}{_LOCK_SUFFIX}"
        assert lock_full in redis.store

        # 1차 해제 (retry_prep) — CorrectionNodes 정상 생성
        state = self._eligible_state()
        state["answer_lock_key"] = lock_key
        nodes = CorrectionNodes(redis=redis)
        await nodes.retry_prep_node(state)
        assert lock_full not in redis.store

        # 2차 해제 (cache_write가 같은 키로 다시 release) — 멱등, 예외 없음
        write_state = self._eligible_state()
        write_state["answer_lock_key"] = lock_key
        write_state["output"]["answer"] = "둘째 패스 답변"
        write_state["vector"]["results"] = [{"service_id": "S2"}]
        write_update = await CacheWriteNode(redis=redis)(write_state)  # no raise
        assert lock_full not in redis.store
        assert write_update["answer_lock_key"] is None

    async def test_graph_zero_hit_retry_does_not_enter_poll(self):
        """그래프 레벨 회귀: C2 0건 1회 재시도 시 poll 타임아웃이 발생하지 않는다.

        RETRIEVE → VECTOR_SEARCH(eligible) → 첫 패스 hydration 0건(C2 게이트) →
        retry_prep(락 해제) → router 재진입 → cache_check 재획득.
        재진입 cache_check 가 SET NX 성공해야 하므로 poll_for_answer 는 호출되지 않는다.
        """
        from agents.graph import AgentGraph
        from schemas.state import ActionType
        from tests.helpers import (
            make_answer_agent,
            make_triage_router,
            run_graph,
        )

        triage, router = make_triage_router(
            ActionType.RETRIEVE,
            IntentType.VECTOR_SEARCH,
            refined_query="서울 테니스장",
        )
        answer_agent = make_answer_agent("테니스장 안내입니다.")

        # vector_agent: refine + embed mock (실제 검색은 hydrate_services 패치로 대체)
        from agents.vector_agent import VectorAgent, _RefinedQuery

        vector_agent = VectorAgent.__new__(VectorAgent)
        refine_chain = MagicMock()
        refine_chain.ainvoke = AsyncMock(
            return_value=_RefinedQuery(
                refined_query="서울 테니스장",
                max_class_name=None,
                area_name=None,
                service_status=None,
            )
        )
        vector_agent._refine_chain = refine_chain
        embeddings = MagicMock()
        embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
        vector_agent._embeddings = embeddings

        redis = _FakeRedis()
        poll_spy = AsyncMock(return_value=None)

        from tests.helpers import make_ai_session

        # vector_search/bm25_search 모두 0건 → hydration 0건 → C2 게이트 발동 →
        # retry_prep(락 해제) → 재진입. retry_count 캡(==1)에 의해 둘째 패스는
        # answer_node 로 통과해 정상 답변을 낸다.
        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents._redis_gateway.poll_for_answer", poll_spy),
        ):
            graph = AgentGraph(
                triage=triage,
                router=router,
                vector_agent=vector_agent,
                answer_agent=answer_agent,
                redis=redis,
            )
            result = await run_graph(
                graph,
                make_agent_state(message="테니스장"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        # 0건 1회 재시도가 발생했고
        assert result["retry_count"] == 1
        # poll 진입 없이 종료 (재진입 cache_check 가 락을 정상 재획득)
        poll_spy.assert_not_called()
        # 최종 답변이 채워졌다
        assert result["output"]["answer"] == "테니스장 안내입니다."
