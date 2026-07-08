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

        from core.cache import _cache_key

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["output"]["answer"] = "신규 답변"
        base_state["vector"]["results"] = [{"service_id": "S1"}]
        with patch(
            "agents._redis_gateway.set_cached_answer_by_key", AsyncMock()
        ) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_called_once()
        args = mock_set.call_args.args
        # signature: set_cached_answer_by_key(store_key, payload, snap, redis)
        # answer_lock_key 미설정 → 재계산 키(K_original)로 폴백 저장
        assert args[0] == _cache_key("서울 테니스장", routes="VECTOR_SEARCH")
        assert args[1]["answer"] == "신규 답변"
        assert args[2]["vector_results"] == [{"service_id": "S1"}]

    async def test_skips_on_error(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["output"]["answer"] = "x"
        base_state["error"] = "boom"
        with patch(
            "agents._redis_gateway.set_cached_answer_by_key", AsyncMock()
        ) as mock_set:
            node = CacheWriteNode(redis=AsyncMock())
            await node(base_state)

        mock_set.assert_not_called()

    async def test_skips_on_cache_hit(self, base_state):
        from agents.nodes import CacheWriteNode

        base_state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        base_state["cache_hit"] = True
        base_state["output"]["answer"] = "x"
        with patch(
            "agents._redis_gateway.set_cached_answer_by_key", AsyncMock()
        ) as mock_set:
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
        with patch(
            "agents._redis_gateway.set_cached_answer_by_key", AsyncMock()
        ) as mock_set:
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
        with patch(
            "agents._redis_gateway.set_cached_answer_by_key", AsyncMock()
        ) as mock_set:
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
# Singleflight 락 누수 회귀 (0건 게이트 → retry_prep 우회 경로)
# ---------------------------------------------------------------------------


class TestSingleflightLockLeak:
    """singleflight 락 수명 정합 검증(락 = 저장 타깃 = K_original 통일 후).

    락은 최초 cache_check 가 K_original 에 획득한 시점부터 cache_write 저장까지
    유지된다. self-correction 재시도 재진입 시 cache_check 는 answer_lock_key 슬롯이
    있으면 재획득·덮어쓰기를 skip 하고(락 정합 유지), retry_prep 는 락을 해제하지
    않는다(락은 전 요청 수명 K_original 유지). cache_write 가 K_original 로 저장·단독
    해제해 저장 키 ↔ 락 키가 정합된다.
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

    async def test_retry_prep_preserves_recorded_lock(self):
        """retry_prep_node는 락을 해제하지 않고 answer_lock_key 슬롯도 건드리지 않는다.

        락은 전 요청 수명 동안 K_original 에 유지되어야 한다(대기자 폴 지속) — retry_prep
        가 미리 풀면 K_original 대기자가 고아가 되고, 재진입 cache_check 가드는 재획득도
        안 하므로 락 정합이 깨진다. 슬롯 미기록(update 에 answer_lock_key 부재)으로
        LangGraph 머지에서 K_original 이 보존된다.
        """
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

        state["answer_lock_key"] = lock_key
        nodes = CorrectionNodes(redis=redis)
        retry_update = await nodes.retry_prep_node(state)

        # 락은 유지되고(해제 안 함), answer_lock_key 슬롯은 update 에 기록되지 않는다
        # (= 머지 무변경 → K_original 보존).
        assert lock_full in redis.store
        assert "answer_lock_key" not in retry_update

    async def test_reentry_cache_check_preserves_key_without_poll(self):
        """재진입 cache_check(answer_lock_key 보유)는 재획득·GET·poll 없이 pass-through.

        완화(K_relaxed)가 있어도 저장 타깃 = K_original 로 고정하려면 재진입 cache_check
        가 슬롯을 덮어쓰지 않고 즉시 miss 로 통과해야 한다. GET/acquire/poll 모두 미호출.
        """
        from agents.nodes import CacheCheckNode
        from agents.nodes.correction import CorrectionNodes

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)

        # 첫 패스: miss → 락 획득
        state = self._eligible_state()
        first = await check(state)
        lock_key = first["answer_lock_key"]

        # 0건 게이트 → retry_prep (락 유지·슬롯 보존)
        state["answer_lock_key"] = lock_key
        nodes = CorrectionNodes(redis=redis)
        await nodes.retry_prep_node(state)

        # 재진입 cache_check: 슬롯 보유 → GET/acquire/poll 미진입, 키 반환 안 함(보존).
        reentry_state = self._eligible_state()
        reentry_state["answer_lock_key"] = lock_key
        # 완화 시뮬레이션: filters 를 바꿔도 저장 타깃은 K_original 이어야 함
        reentry_state["filters"]["area_name"] = "성동구"
        get_mock = AsyncMock()
        poll_mock = AsyncMock()
        with (
            patch("agents._redis_gateway.get_cached_answer_by_key", get_mock),
            patch("agents._redis_gateway.poll_for_answer", poll_mock),
        ):
            reentry = await check(reentry_state)

        get_mock.assert_not_called()
        poll_mock.assert_not_called()
        assert reentry["cache_hit"] is False
        # 슬롯을 반환하지 않는다(머지 무변경 → K_original 보존)
        assert "answer_lock_key" not in reentry

    async def test_cache_write_stores_and_releases_at_recorded_key(self):
        """cache_write는 answer_lock_key(K_original)로 저장하고 같은 키로 락을 해제한다."""
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from core.cache import _LOCK_SUFFIX, get_cached_answer_by_key

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

        # 저장이 K_original(= lock_key)에 이뤄졌다
        envelope = await get_cached_answer_by_key(lock_key, redis)
        assert envelope is not None
        assert envelope["payload"]["answer"] == "신규 답변"
        # 같은 키로 락이 해제됐다
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

        # retry_prep도 no-op(락 미해제, raise 없음) — CorrectionNodes 정상 생성
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

    async def test_redis_failure_records_key_but_cache_write_release_swallows(self):
        """Redis 장애 fail-open: acquire=True로 키 기록되나 cache_write release는 예외 삼킴.

        acquire가 SET 예외 시 True(fail-open)를 반환 → cache_check가 lock_key를
        기록한다. retry_prep는 락을 건드리지 않는다(예외 미발생). 이후 cache_write의
        release가 DEL에서 예외를 던져도 메인 흐름이 막히지 않아야 한다.
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode
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

        # retry_prep는 락을 건드리지 않는다(DEL 미호출 → 예외 없음)
        state = self._eligible_state()
        state["answer_lock_key"] = check_result["answer_lock_key"]
        nodes = CorrectionNodes(redis=redis)
        retry_update = await nodes.retry_prep_node(state)  # no raise
        assert "answer_lock_key" not in retry_update

        # cache_write의 저장(SET 예외)·release(DEL 예외)가 삼켜져 메인 흐름이 안 막힘
        write_state = self._eligible_state()
        write_state["answer_lock_key"] = check_result["answer_lock_key"]
        write_state["output"]["answer"] = "답변"
        write_state["vector"]["results"] = [{"service_id": "S1"}]
        write_update = await CacheWriteNode(redis=redis)(write_state)  # no raise
        assert write_update["answer_lock_key"] is None

    async def test_double_release_is_harmless(self):
        """이중 해제 무해성: 동일 락 키에 release가 두 번 호출돼도 DEL은 멱등(no-op).

        cache_write 가 K_original 락을 해제한 뒤(정상 종단) 어떤 경로에서 같은 키를
        다시 release 해도 이미 없는 키 → 예외/오류가 없다.
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from core.cache import _LOCK_SUFFIX, release_answer_lock

        redis = _FakeRedis()
        check_result = await CacheCheckNode(redis=redis)(self._eligible_state())
        lock_key = check_result["answer_lock_key"]
        lock_full = f"{lock_key}{_LOCK_SUFFIX}"
        assert lock_full in redis.store

        # 1차 해제 (cache_write 저장 후 단독 해제)
        write_state = self._eligible_state()
        write_state["answer_lock_key"] = lock_key
        write_state["output"]["answer"] = "답변"
        write_state["vector"]["results"] = [{"service_id": "S2"}]
        write_update = await CacheWriteNode(redis=redis)(write_state)
        assert lock_full not in redis.store
        assert write_update["answer_lock_key"] is None

        # 2차 해제 (같은 키로 다시 release) — 멱등, 예외 없음
        await release_answer_lock(lock_key, redis)  # no raise
        assert lock_full not in redis.store

    async def test_graph_zero_hit_retry_does_not_enter_poll(self):
        """그래프 레벨 회귀: 0건 1회 재시도 시 poll 타임아웃이 발생하지 않는다.

        RETRIEVE → VECTOR_SEARCH(eligible) → 첫 패스 hydration 0건(0건 게이트) →
        retry_prep(락 유지) → router 재진입 → cache_check 재진입.
        재진입 cache_check 는 answer_lock_key 슬롯을 보고 즉시 pass-through 하므로
        재획득도 poll 도 하지 않는다(poll_for_answer 미호출).
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

        # vector_search/bm25_search 모두 0건 → hydration 0건 → 0건 게이트 발동 →
        # retry_prep(락 유지) → 재진입. retry_count 캡(==1)에 의해 둘째 패스는
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
        # poll 진입 없이 종료 (재진입 cache_check 가드가 즉시 pass-through)
        poll_spy.assert_not_called()
        # 최종 답변이 채워졌다
        assert result["output"]["answer"] == "테니스장 안내입니다."


# ---------------------------------------------------------------------------
# 캐시 키 정합 회귀 (선재 버그): 완화 재시도 후 저장 키 = 최초 질의 키(K_original)
# ---------------------------------------------------------------------------


class TestCacheKeyConsistencyAcrossRetry:
    """self-correction 완화 재시도로 filters/intent 가 갈려도 저장 키를 K_original 로 고정.

    버그: cache_write 가 현재(완화) state 로 키를 재계산(K_relaxed)해 저장하면
      (1) 결정적 refine 이 원 질의로 항상 K_original 을 재생성하는데 답변은 K_relaxed 에
          있어 동일 질의가 계속 miss → 재시도 사이클 반복,
      (2) K_original 을 폴링하던 singleflight 대기자가 고아가 됨.
    수정: cache_write 는 answer_lock_key(= 최초 cache_check 시점 K_original)로 저장.
    """

    def _eligible_state(self) -> AgentState:
        state = make_agent_state(message="강남 테니스장", refined_query="서울 테니스장")
        state["plan"]["intent"] = IntentType.VECTOR_SEARCH
        state["triage"]["action"] = None  # RETRIEVE 게이트 통과
        return state

    async def test_relaxed_retry_stores_at_original_key_and_same_query_hits(self):
        """⑴ 완화 재시도(필터 드롭) 후 저장 → 동일 원 질의 재요청이 cache hit.

        1) 최초 cache_check: filters(area_name) 포함 K_original 로 락 획득·키 기록.
        2) 완화 재시도: filters 를 드롭한 채로 cache_write 도달(K_relaxed 상이).
        3) cache_write 는 answer_lock_key(K_original)로 저장해야 한다.
        4) 동일 원 질의(= 최초 filters 재현)로 cache_check 하면 hit.
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode

        redis = _FakeRedis()
        check = CacheCheckNode(redis=redis)

        # 1) 최초 cache_check — area_name 필터 포함 → K_original 획득·기록
        first_state = self._eligible_state()
        first_state["filters"]["area_name"] = "강남구"
        first = await check(first_state)
        k_original = first["answer_lock_key"]
        assert k_original is not None

        # 2) 완화 재시도 시뮬레이션: filters 드롭 후 cache_write 도달.
        #    (retry_prep 가 area_name 을 None 으로 드롭 → 현재 state 재계산 시 K_relaxed)
        write_state = self._eligible_state()
        write_state["filters"]["area_name"] = None  # 완화(드롭)
        write_state["answer_lock_key"] = k_original  # 재진입 가드로 보존된 K_original
        write_state["output"]["answer"] = "완화 후 테니스장 안내"
        write_state["vector"]["results"] = [{"service_id": "S1"}]
        await CacheWriteNode(redis=redis)(write_state)

        # 3) 저장이 K_relaxed 가 아닌 K_original 에 이뤄졌다
        from core.cache import _cache_key

        k_relaxed = _cache_key("서울 테니스장", routes="VECTOR_SEARCH")  # area 없음
        assert k_original != k_relaxed  # 두 키가 실제로 갈린다(전제 성립)
        assert k_relaxed not in redis.store  # 완화 키에는 저장 안 됨
        assert k_original in redis.store

        # 4) 동일 원 질의(최초 filters 재현)로 cache_check → hit (miss 반복 없음)
        replay = self._eligible_state()
        replay["filters"]["area_name"] = "강남구"  # 결정적 refine + 동일 원 요청
        hit = await CacheCheckNode(redis=redis)(replay)
        assert hit["cache_hit"] is True
        assert hit["output"]["answer"] == "완화 후 테니스장 안내"

    async def test_singleflight_waiter_polling_original_key_hits(self):
        """⑵ K_original 을 폴링하던 singleflight 대기자가 완화 저장 후에도 hit.

        보유자가 완화 재시도를 거쳐 K_original 에 저장·해제하므로, 최초 질의 키를
        폴링하던 대기자가 그 키에서 결과를 본다(고아 미발생).
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from core.cache import poll_for_answer

        redis = _FakeRedis()

        # 보유자: 최초 cache_check(K_original 획득)
        holder_state = self._eligible_state()
        holder_state["filters"]["area_name"] = "강남구"
        holder = await CacheCheckNode(redis=redis)(holder_state)
        k_original = holder["answer_lock_key"]

        # 대기자: 같은 원 질의 → K_original 락 점유(acquire 실패). 아직 결과 없음.
        assert await poll_for_answer(k_original, redis, retries=1, interval=0) is None

        # 보유자가 완화 재시도를 거쳐 cache_write(K_original 저장·해제)
        write_state = self._eligible_state()
        write_state["filters"]["area_name"] = None  # 완화
        write_state["answer_lock_key"] = k_original
        write_state["output"]["answer"] = "보유자 답변"
        write_state["vector"]["results"] = [{"service_id": "S1"}]
        await CacheWriteNode(redis=redis)(write_state)

        # 대기자가 K_original 을 다시 폴하면 이제 hit(envelope 수신)
        polled = await poll_for_answer(k_original, redis, retries=1, interval=0)
        assert polled is not None
        assert polled["payload"]["answer"] == "보유자 답변"

    async def test_normal_path_store_key_equals_recomputed_key(self):
        """⑶ 정상(비재시도) 경로 동작 불변: K_original == K_final.

        재시도가 없으면 저장 키(answer_lock_key)와 현재 state 재계산 키가 동일하므로
        저장 위치가 수정 전과 같다(회귀 금지). 재계산 키에서 hit 이 확인된다.
        """
        from agents.nodes import CacheCheckNode, CacheWriteNode
        from core.cache import _cache_key, get_cached_answer_by_key

        redis = _FakeRedis()
        state = self._eligible_state()
        state["filters"]["area_name"] = "강남구"

        first = await CacheCheckNode(redis=redis)(state)
        k_original = first["answer_lock_key"]

        # 완화 없이 그대로 cache_write (동일 filters 유지)
        state["answer_lock_key"] = k_original
        state["output"]["answer"] = "정상 답변"
        state["vector"]["results"] = [{"service_id": "S1"}]
        await CacheWriteNode(redis=redis)(state)

        # 현재 state 로 재계산한 키와 저장 키가 동일하다
        recomputed = _cache_key(
            "서울 테니스장", area_name="강남구", routes="VECTOR_SEARCH"
        )
        assert k_original == recomputed
        envelope = await get_cached_answer_by_key(recomputed, redis)
        assert envelope is not None
        assert envelope["payload"]["answer"] == "정상 답변"

    async def test_cache_write_falls_back_to_recomputed_key_without_lock(self):
        """answer_lock_key 부재(구 경로/poll-timeout fail-open) → 재계산 키로 폴백 저장.

        하위호환: 락 미보유로 슬롯이 None 이면 기존처럼 현재 state 로 키를 재계산해
        저장한다(정상 경로 동작 보존).
        """
        from agents.nodes import CacheWriteNode
        from core.cache import _cache_key, get_cached_answer_by_key

        redis = _FakeRedis()
        state = self._eligible_state()
        state["filters"]["area_name"] = "강남구"
        # answer_lock_key 미설정(None)
        state["output"]["answer"] = "폴백 답변"
        state["vector"]["results"] = [{"service_id": "S1"}]
        await CacheWriteNode(redis=redis)(state)

        recomputed = _cache_key(
            "서울 테니스장", area_name="강남구", routes="VECTOR_SEARCH"
        )
        envelope = await get_cached_answer_by_key(recomputed, redis)
        assert envelope is not None
        assert envelope["payload"]["answer"] == "폴백 답변"
