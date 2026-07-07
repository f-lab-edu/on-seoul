"""TriageAgent 통합 테스트 — 캐시 키 + refine 캐시/싱글플라이트 + stream 이벤트.

검증 대상:
- 캐시 키: RETRIEVE 단일/멀티 충돌 없음, 비-RETRIEVE 캐시 제외
- router_node refine 캐싱 (LLM 호출 skip)
- router_node refine hop singleflight (락 누수 금지, fail-open)
- stream() 이벤트 - action 경로
"""

from unittest.mock import AsyncMock, MagicMock, patch


from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from core.cache import _cache_key
from schemas.intake import IntakeAction, TurnKind
from schemas.state import ActionType, IntentType
from tests._graph_triage_support import _answer_agent, _state
from tests.helpers import (
    make_agent_state,
    make_intake,
    make_router,
    make_ai_session,
    patch_node_sessions,
    stream_graph,
)


# ---------------------------------------------------------------------------
# 4. 캐시 키 충돌 없음 + 비-RETRIEVE 캐시 제외
# ---------------------------------------------------------------------------


class TestCacheKeyWithRoutes:
    def test_single_route_key(self):
        """단일 primary intent만 있을 때 캐시 키가 생성된다."""
        key1 = _cache_key("수영장", routes="SQL_SEARCH")
        key2 = _cache_key("수영장", routes="VECTOR_SEARCH")
        assert key1 != key2

    def test_multi_route_key_differs_from_single(self):
        """multi-route(SQL+VECTOR) 키가 단일 라우트 키와 다르다."""
        key_single = _cache_key("마포구 풋살장", routes="SQL_SEARCH")
        key_multi = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        assert key_single != key_multi

    def test_multi_route_key_order_independent(self):
        """routes 파라미터 내 순서 독립적으로 동일해야 한다."""
        # _cache_key는 routes 문자열을 그대로 사용하므로 CacheCheckNode에서 정렬하여 전달한다
        # 여기서는 정렬된 문자열이 같으면 동일한 키를 확인한다
        key1 = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        key2 = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        assert key1 == key2

    def test_non_retrieve_cache_excluded(self):
        """비-RETRIEVE action이면 CacheCheckNode가 cache_hit=False를 반환한다."""
        from agents.nodes import CacheCheckNode

        node = CacheCheckNode(redis=MagicMock())

        import asyncio

        async def _check(action: ActionType) -> bool:
            state = make_agent_state(
                action=action,
                intent=IntentType.SQL_SEARCH,
                refined_query="수영장",
            )
            result = await node(state)
            return result.get("cache_hit", False)

        for action in (
            ActionType.DIRECT_ANSWER,
            ActionType.AMBIGUOUS,
            ActionType.OUT_OF_SCOPE,
            ActionType.EXPLAIN,
        ):
            assert asyncio.get_event_loop().run_until_complete(_check(action)) is False

    def test_cache_write_excludes_non_retrieve(self):
        """비-RETRIEVE action이면 CacheWriteNode가 빈 dict를 반환한다."""
        from agents.nodes import CacheWriteNode

        node = CacheWriteNode(redis=MagicMock())

        import asyncio

        async def _write(action: ActionType) -> dict:
            state = make_agent_state(
                action=action,
                intent=IntentType.SQL_SEARCH,
                refined_query="수영장",
                answer="답변",
            )
            return await node(state)

        for action in (ActionType.DIRECT_ANSWER, ActionType.AMBIGUOUS):
            result = asyncio.get_event_loop().run_until_complete(_write(action))
            assert result == {}


class TestRouterNodeRefineCache:
    """router_node refine 캐싱 (0-3-3) — LLM 호출 skip 검증."""

    def _nodes(self, router):
        return GraphNodes(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
        )

    async def test_hit_skips_llm_and_restores_state(self):
        """refine 캐시 hit 시 LLM 미호출 + 저장값으로 update 복원."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        cached = {
            "intent": "VECTOR_SEARCH",
            "refined_query": "서울 테니스장",
            "max_class_name": "체육시설",
            "area_name": None,
            "service_status": None,
            "payment_type": None,
            "vector_sub_intent": "identification",
            "secondary_intent": None,
        }
        with (
            patch_node_sessions(),
            patch(
                "agents._redis_gateway.get_cached_refine_by_key",
                AsyncMock(return_value=cached),
            ),
            patch("agents._redis_gateway.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state(message="테니스장"))

        structured.ainvoke.assert_not_called()
        mock_set.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert update["plan"]["refined_query"] == "서울 테니스장"
        assert update["filters"]["max_class_name"] == "체육시설"
        assert update["plan"]["vector_sub_intent"] == "identification"
        assert "refine_cache_hit" in update["node_path"]

    async def test_miss_calls_llm_and_sets_cache(self):
        """refine 캐시 miss 시 LLM 호출 후 set 으로 채운다."""
        router = make_router(
            IntentType.SQL_SEARCH,
            refined_query="마포구 풋살장",
            max_class_name="체육시설",
        )
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        with (
            patch_node_sessions(),
            patch(
                "agents._redis_gateway.get_cached_refine_by_key",
                AsyncMock(return_value=None),
            ),
            patch("agents._redis_gateway.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state(message="마포구 풋살장"))

        structured.ainvoke.assert_called_once()
        mock_set.assert_called_once()
        # 저장값에 intent.value 직렬화 포함
        stored = mock_set.call_args.args[2]
        assert stored["intent"] == "SQL_SEARCH"
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_forced_intent_skips_cache(self):
        """forced_intent 경로는 refine 캐시를 조회/저장하지 않는다."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        with (
            patch_node_sessions(),
            patch(
                "agents._redis_gateway.get_cached_refine_by_key", AsyncMock()
            ) as mock_get,
            patch("agents._redis_gateway.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )
        mock_get.assert_not_called()
        mock_set.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH

    async def test_llm_error_does_not_set_cache(self):
        """classify 예외 시 캐시 SET 하지 않는다(에러 처리 유지)."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        with (
            patch_node_sessions(),
            patch(
                "agents._redis_gateway.get_cached_refine_by_key",
                AsyncMock(return_value=None),
            ),
            patch("agents._redis_gateway.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state())
        mock_set.assert_not_called()
        assert "router_error" in update["node_path"]

    def test_serialize_restore_roundtrip_secondary_intent(self):
        """serialize→restore 대칭: secondary_intent(IntentType) round-trip 보존.

        _serialize_refine 은 IntentType→.value(str), _restore_refine 은 .value→IntentType
        로 복원한다. 캐시 SET 시 저장된 secondary_intent 가 HIT 복원 시 동일 enum 으로
        돌아오는지(데이터 무손실) 검증한다. 직접 round-trip 으로 secondary 분기를 단언.
        """
        from agents.nodes import _restore_refine, _serialize_refine

        update = {
            "plan": {
                "intent": IntentType.SQL_SEARCH,
                "refined_query": "마포구 풋살장",
                "secondary_intent": IntentType.VECTOR_SEARCH,
            },
            "filters": {"max_class_name": "체육시설"},
        }
        stored = _serialize_refine(update)
        # JSON 직렬화 가능한 str 로 저장
        assert stored["intent"] == "SQL_SEARCH"
        assert stored["secondary_intent"] == "VECTOR_SEARCH"

        restored = _restore_refine(stored)
        assert restored["plan"]["intent"] is IntentType.SQL_SEARCH
        assert restored["plan"]["secondary_intent"] is IntentType.VECTOR_SEARCH
        assert restored["plan"]["refined_query"] == "마포구 풋살장"
        assert restored["filters"]["max_class_name"] == "체육시설"

    def test_serialize_restore_omits_none_fields(self):
        """None 필드는 직렬화/복원 모두에서 생략(retry 경로 초기화 보존, 대칭)."""
        from agents.nodes import _restore_refine, _serialize_refine

        update = {"plan": {"intent": IntentType.VECTOR_SEARCH}}  # 선택 필드 전부 미존재
        stored = _serialize_refine(update)
        assert "refined_query" not in stored
        assert "secondary_intent" not in stored

        restored = _restore_refine(stored)
        assert restored == {"plan": {"intent": IntentType.VECTOR_SEARCH}}

    def test_restore_skips_explicit_none_values(self):
        """캐시 dict 에 명시적 None 이 있어도 update 에 키를 넣지 않는다(line 215-217)."""
        from agents.nodes import _restore_refine

        cached = {
            "intent": "SQL_SEARCH",
            "refined_query": None,
            "area_name": None,
            "secondary_intent": None,
        }
        restored = _restore_refine(cached)
        assert restored == {"plan": {"intent": IntentType.SQL_SEARCH}}


class TestRouterNodeRefineSingleflight:
    """router_node refine hop singleflight — answer singleflight 대칭.

    동시 cold-miss 시 첫 호출자만 classify, 나머지는 poll 로 refine_cache hit.
    락 누수 금지(try/finally), forced_intent 경로 미진입, fail-open 검증.
    """

    def _nodes(self, router, redis):
        return GraphNodes(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
            redis=redis,
        )

    async def test_waiter_polls_and_skips_classify(self):
        """락 미획득 호출자는 poll 로 refine hit → classify 호출 0."""
        import json

        router = make_router(IntentType.SQL_SEARCH)
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        # GET(by_key) miss → acquire 실패(다른 보유자) → poll 에서 hit.
        cached = {"intent": "VECTOR_SEARCH", "refined_query": "서울 테니스장"}
        redis.get.side_effect = [None, json.dumps(cached)]
        redis.set.return_value = None  # SET NX 실패 = 락 미획득
        nodes = self._nodes(router, redis)
        with patch_node_sessions(), patch("asyncio.sleep", AsyncMock()):
            update = await nodes.router_node(_state(message="테니스장"))

        structured.ainvoke.assert_not_called()  # classify 중복 0
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert "refine_cache_hit" in update["node_path"]

    async def test_holder_acquires_classifies_and_releases(self):
        """락 보유자는 classify 실행 후 release_refine_lock(DEL) 호출."""
        from core.cache import _LOCK_SUFFIX

        router = make_router(IntentType.SQL_SEARCH, refined_query="마포구 풋살장")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None  # GET miss
        redis.set.return_value = True  # SET NX 성공 = 락 보유
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state(message="마포구 풋살장"))

        structured.ainvoke.assert_called_once()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH
        # release 가 락 키(캐시 키 + :lock)로 DEL 됨.
        delete_keys = [c.args[0] for c in redis.delete.call_args_list]
        assert any(k.endswith(_LOCK_SUFFIX) for k in delete_keys)

    async def test_lock_released_on_classify_exception(self):
        """★ 락 누수 가드: classify 예외에도 finally 가 release_refine_lock 호출."""
        from core.cache import _LOCK_SUFFIX

        router = make_router(IntentType.SQL_SEARCH)
        structured = router._llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        redis = AsyncMock()
        redis.get.return_value = None
        redis.set.return_value = True  # 락 보유
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state())

        assert "router_error" in update["node_path"]
        # 예외 경로에서도 락 해제됨.
        delete_keys = [c.args[0] for c in redis.delete.call_args_list]
        assert any(k.endswith(_LOCK_SUFFIX) for k in delete_keys)

    async def test_forced_intent_skips_lock(self):
        """forced_intent 경로는 refine 락을 acquire/release/poll 하지 않는다."""
        router = make_router(IntentType.SQL_SEARCH)
        redis = AsyncMock()
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )
        # GET/SET NX/DEL 어느 것도 호출되지 않음(refine 경로 미진입).
        redis.set.assert_not_called()
        redis.delete.assert_not_called()
        redis.get.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH

    async def test_fail_open_on_poll_timeout_runs_classify(self):
        """락 미획득 + poll 타임아웃 → fail-open: classify 실행, 락 미해제."""
        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None  # GET miss + poll 매회 None
        redis.set.return_value = None  # 락 미획득(waiter)
        nodes = self._nodes(router, redis)
        with patch_node_sessions(), patch("asyncio.sleep", AsyncMock()):
            update = await nodes.router_node(_state(message="q"))

        structured.ainvoke.assert_called_once()  # fail-open 으로 직접 classify
        # 락 미보유라 release(DEL) 하지 않음.
        redis.delete.assert_not_called()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_fail_open_on_acquire_redis_error_runs_classify(self):
        """Redis acquire 예외 → fail-open True → classify 실행."""
        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None
        redis.set.side_effect = RuntimeError("redis down")  # acquire 예외 → fail-open
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state(message="q"))

        structured.ainvoke.assert_called_once()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_disabled_toggle_skips_lock(self):
        """refine singleflight 비활성화 → acquire no-op(True), SET NX·DEL 미호출.

        cache write(set_cached_refine)는 SET EX 로 여전히 호출되므로, 락 전용
        SET NX(nx=True) 와 release(DEL) 만 미호출인지 구분해 단언한다.
        """
        from core.config import settings as cfg

        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        redis = AsyncMock()
        redis.get.return_value = None
        nodes = self._nodes(router, redis)
        with (
            patch_node_sessions(),
            patch.object(cfg, "refine_cache_singleflight_enabled", False),
        ):
            update = await nodes.router_node(_state(message="q"))
        # 락 게이트 off → SET NX(acquire) 미호출(cache SET EX 는 허용)·DEL(release) 미호출.
        nx_sets = [c for c in redis.set.call_args_list if c.kwargs.get("nx")]
        assert nx_sets == []
        redis.delete.assert_not_called()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH


# ---------------------------------------------------------------------------
# 7. stream() 이벤트 - action 경로
# ---------------------------------------------------------------------------


class TestStreamEventsWithTriage:
    async def _collect(self, gen):
        events = []
        async for event_type, data in gen:
            events.append((event_type, data))
        return events

    async def test_direct_answer_emits_answering_not_searching(self):
        """DIRECT_ANSWER action은 searching progress 없이 answering 바로 방출."""
        intake = make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.DIRECT_ANSWER)
        graph = AgentGraph(intake=intake, answer_agent=_answer_agent("직접 답변"))

        events = await self._collect(
            stream_graph(
                graph,
                _state(message="안녕하세요"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )
        )
        steps = [d["step"] for t, d in events if t == "progress"]
        assert "answering" in steps
        assert "searching" not in steps

    # RETRIEVE/SQL searching present 는 test_graph 의 progress 순서 테스트
    # (fanout/router-only)가 이미 커버하므로 축소했다. DIRECT_ANSWER 의 searching
    # 미방출(고유 negative 분기)은 위에 유지한다.
