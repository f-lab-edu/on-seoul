"""계획 페이즈 — router 노드 + 검색 라우팅 엣지 + refine 캐시 직렬화.

입구 분류(action/turn_kind)와 참조 바인딩은 intake 페이즈(agents/nodes/intake.py)로
이관됐다. 이 페이즈는 RETRIEVE 확정 후의 검색 *계획*(intent/필터/refined_query)과
intent 기반 라우팅만 담당한다.
"""

import logging
from typing import Any

from agents import _emit, _redis_gateway
from agents.router_agent import RouterAgent
from agents.triage_agent import TriageAgent
from core.config import settings
from schemas.state import ActionType, AgentState, IntentType

logger = logging.getLogger(__name__)

# refine 캐시 직렬화에서 plan(머지) 채널로 가는 필드.
_REFINE_PLAN_FIELDS: tuple[str, ...] = (
    "refined_query",
    "vector_sub_intent",
)
# refine 캐시 직렬화에서 filters(머지) 채널로 가는 필드.
_REFINE_FILTER_FIELDS: tuple[str, ...] = (
    "max_class_name",
    "area_name",
    "service_status",
    "payment_type",
    "target_audience",
)


def _build_router_update(result: Any) -> dict[str, Any]:
    """RouterAgent.classify 결과 → router_node update dict (중첩 채널).

    None 필드는 포함하지 않아 retry 경로에서 초기화된 값을 덮어쓰지 않는다(머지 보존).
    intent 는 항상 포함하고 node_path 는 호출 측이 설정한다.
    plan/filters 는 dict_merge 채널이므로 부분 기록만 보낸다.
    """
    plan: dict[str, Any] = {"intent": result.intent}
    if result.refined_query is not None:
        plan["refined_query"] = result.refined_query
    if result.vector_sub_intent is not None:
        plan["vector_sub_intent"] = result.vector_sub_intent
    if result.secondary_intent is not None:
        plan["secondary_intent"] = result.secondary_intent

    filters: dict[str, Any] = {}
    if result.max_class_name is not None:
        filters["max_class_name"] = result.max_class_name
    if result.area_name is not None:
        filters["area_name"] = result.area_name
    if result.service_status is not None:
        filters["service_status"] = result.service_status
    if result.payment_type is not None:
        filters["payment_type"] = result.payment_type
    if result.target_audience is not None:
        filters["target_audience"] = result.target_audience

    update: dict[str, Any] = {"plan": plan}
    if filters:
        update["filters"] = filters
    return update


def _serialize_refine(update: dict[str, Any]) -> dict[str, Any]:
    """router_node update → refine 캐시 저장 dict (IntentType → .value).

    update 는 {plan: {...}, filters: {...}} 중첩 구조. refine 캐시는 AgentState 와
    독립된 평면 redis dict 이므로 단일 평면 dict 로 직렬화한다(_restore_refine 과 대칭).
    intent 는 _IntentOutput.intent 가 required 이므로 구조적으로 항상 non-None.
    """
    plan: dict[str, Any] = update.get("plan", {})
    filters: dict[str, Any] = update.get("filters", {})
    intent: IntentType = plan["intent"]
    stored: dict[str, Any] = {"intent": intent.value}
    for field in _REFINE_PLAN_FIELDS:
        if field in plan:
            stored[field] = plan[field]
    for field in _REFINE_FILTER_FIELDS:
        if field in filters:
            stored[field] = filters[field]
    secondary = plan.get("secondary_intent")
    if secondary is not None:
        stored["secondary_intent"] = secondary.value
    return stored


def _restore_refine(cached: dict[str, Any]) -> dict[str, Any]:
    """refine 캐시 dict → router_node update dict (.value → IntentType, 중첩 채널).

    저장값이 None 인 필드는 update 에서 생략한다(retry 경로 초기화 보존, 직렬화 대칭).
    """
    plan: dict[str, Any] = {"intent": IntentType(cached["intent"])}
    for field in _REFINE_PLAN_FIELDS:
        val = cached.get(field)
        if val is not None:
            plan[field] = val
    secondary = cached.get("secondary_intent")
    if secondary is not None:
        plan["secondary_intent"] = IntentType(secondary)

    filters: dict[str, Any] = {}
    for field in _REFINE_FILTER_FIELDS:
        val = cached.get(field)
        if val is not None:
            filters[field] = val

    update: dict[str, Any] = {"plan": plan}
    if filters:
        update["filters"] = filters
    return update


class PlanningNodes:
    """계획 페이즈 — router 노드(RETRIEVE 검색 계획) + 검색 라우팅 엣지.

    입구 분류(action/turn_kind)·참조 바인딩은 intake 페이즈로 이관됐고, 이 페이즈는
    RETRIEVE 확정 후의 검색 계획(intent/필터/refined_query)과 intent 라우팅만 담당한다.

    의존: router(RouterAgent), redis(refine 캐시). triage(TriageAgent)는 하위호환
    주입 슬롯으로만 보유하며 이 페이즈의 노드는 더 이상 호출하지 않는다.
    """

    def __init__(
        self,
        triage: TriageAgent | None,
        router: RouterAgent | None,
        redis: Any,
    ) -> None:
        self._triage = triage
        self._router = router
        self._redis = redis

    @staticmethod
    def _route_fallback_breadcrumb(state: AgentState) -> list[str]:
        """RETRIEVE 가 아닌 action 이 router_node 에 도달한 fallback 진입 마커.

        router_node 의 forced 분기 이후에만 호출되므로 forced=None 이 확정이다. route_intake
        의 미처리 분기(미지 turn_kind/action)가 router_node 로 수렴할 때, 정상 검색과
        구분하도록 node_path 에 breadcrumb 를 남긴다(관측용 공유상태 기록).
        """
        action = state["triage"].get("action")
        return ["route_unknown_action"] if action != ActionType.RETRIEVE else []

    async def router_node(self, state: AgentState) -> dict[str, Any]:
        """RouterAgent.classify() 호출 — 검색 계획 수립.

        RETRIEVE action 으로 판정된 경우에만 실행된다(route_by_action → router_node).
        intent / refined_query / post-filter / secondary_intent 를 설정한다.

        refined_query 는 Router 가 산출하여 후속 cache_check_node 가 정확한 키 기반
        lookup 을 수행할 수 있도록 한다. None 이면 cache_check 는 pass-through 되며
        VectorAgent 가 자체 refine 체인으로 대체 산출한다.

        forced_intent honor (triage_node 에서 이관):
            retry_prep_node 가 방향성 재시도로 intent 를 강제하면 LLM 재분류를 skip 하고
            그 intent 를 그대로 반환한다. forced_intent 는 즉시 None 으로 소비(1회성)하여
            무한 전환을 막는다. refined_query/post-filter 는 채우지 않으므로 cache_check 는
            pass-through 되고(0건이던 원 질의 오hit 방지), 전환된 경로(VECTOR)가 자체 정제한다.
        """
        forced = state.get("forced_intent")
        if forced is not None:
            logger.info(
                "router.forced room=%s intent=%s",
                state.get("room_id"),
                forced.value,
            )
            update = {
                "plan": {"intent": forced},
                "forced_intent": None,
                "node_path": ["router"],
            }
            update.update(_emit.emit_router_events(state, update))
            return update

        if self._router is None:
            # RETRIEVE 로 판정됐으나 RouterAgent 미주입 — 안전망으로 FALLBACK 처리.
            logger.warning("router_node — RouterAgent 미주입, intent=FALLBACK 처리")
            update = {"plan": {"intent": IntentType.FALLBACK}, "node_path": ["router"]}
            update.update(_emit.emit_router_events(state, update))
            return update

        # (0-3-3) refine 캐시 — raw query(+history) 기준 LLM(검색 계획) 결과 공유.
        # forced_intent 분기 이후, classify 이전에 GET. 적중 시 LLM skip.
        # singleflight(answer 대칭): 동시 cold-miss 시 첫 호출자만 classify 실행,
        # 나머지는 poll 로 refine_cache hit. 락은 try/finally 로 성공·예외 모두 해제
        # (락 누수 금지). 키는 한 번만 계산해 GET/lock/poll/SET/release 에 재사용.
        message = state["message"]
        history = state.get("history") or []
        redis = self._redis
        key = _redis_gateway.build_refine_cache_key(message, history)
        cached = await _redis_gateway.get_cached_refine_by_key(key, redis)
        if cached is not None:
            return self._refine_cache_hit_update(state, cached)

        # singleflight: 첫 miss 호출자만 classify, 나머지는 결과 대기.
        acquired = await _redis_gateway.acquire_refine_lock(
            key, redis, ttl=settings.refine_cache_lock_ttl
        )
        if not acquired:
            logger.info(
                "router.refine_singleflight.wait room=%s", state.get("room_id")
            )
            polled = await _redis_gateway.poll_for_refine(
                key,
                redis,
                retries=settings.refine_cache_lock_poll_retries,
                interval=settings.refine_cache_lock_poll_interval,
            )
            if polled is not None:
                logger.info(
                    "router.refine_singleflight.hit room=%s", state.get("room_id")
                )
                return self._refine_cache_hit_update(state, polled)
            # poll 타임아웃 → fail-open: 아래로 진행해 직접 classify(락 미보유).
            logger.info(
                "router.refine_singleflight.timeout room=%s", state.get("room_id")
            )

        # 락 보유(acquired) 또는 fail-open(poll 타임아웃) → 직접 classify.
        # ★ 락 누수 가드: acquired 인 경우에만 finally 에서 release(획득 플래그 추적).
        try:
            result = await self._router.classify(
                message,
                history=history,
            )
            update = _build_router_update(result)
            update["node_path"] = ["router", *self._route_fallback_breadcrumb(state)]
            # miss → 정상 update 구성 후 SET. classify 예외 시 SET 안 함(아래 except).
            await _redis_gateway.set_cached_refine(
                message, history, _serialize_refine(update), redis
            )
            logger.info(
                "router.classify room=%s intent=%s secondary=%s refined=%r "
                "max_class=%s area=%s status=%s",
                state.get("room_id"),
                result.intent.value,
                result.secondary_intent.value if result.secondary_intent else None,
                (result.refined_query or "")[:40],
                result.max_class_name,
                result.area_name,
                result.service_status,
            )
            update.update(_emit.emit_router_events(state, update))
            return update
        except Exception as exc:
            logger.exception("router_node 실행 오류")
            err_update: dict[str, Any] = {
                "error": str(exc),
                "output": {
                    "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                },
                "node_path": ["router_error"],
            }
            # intent 미확정(plan 없음) → answering emit.
            err_update.update(_emit.emit_router_events(state, err_update))
            return err_update
        finally:
            # ★ 락 누수 가드: classify 성공·예외·early-return 어디서도 락을 해제한다.
            # acquired(획득 플래그)인 경우에만 release — fail-open(poll 타임아웃) 경로는
            # 락 미보유라 release 하지 않는다(없는 락 DEL 회피).
            if acquired:
                await _redis_gateway.release_refine_lock(key, redis)

    def _refine_cache_hit_update(
        self, state: AgentState, cached: dict[str, Any]
    ) -> dict[str, Any]:
        """refine 캐시 hit(GET 또는 singleflight poll) 공통 복원 경로.

        저장된 평면 dict 를 router_node update(중첩 채널)로 복원하고
        refine_cache_hit node_path + router 이벤트를 머지한다.
        """
        logger.info(
            "router.refine_cache_hit room=%s intent=%s",
            state.get("room_id"),
            cached.get("intent"),
        )
        update = _restore_refine(cached)
        update["node_path"] = [
            "router",
            "refine_cache_hit",
            *self._route_fallback_breadcrumb(state),
        ]
        update.update(_emit.emit_router_events(state, update))
        return update

    # ------------------------------------------------------------------
    # 엣지 로직 (라우팅)
    # ------------------------------------------------------------------

    def route_by_action_fanout(self, state: AgentState) -> list[str] | str:
        """RETRIEVE 경로 내 secondary_intent 팬아웃 분기.

        enable_secondary_intent=True이고 secondary_intent가 있으면 SQL+VECTOR 병렬 팬아웃.
        그 외에는 route_by_intent(기존 단일 라우트).

        LangGraph 조건부 엣지가 list를 반환하면 병렬 팬아웃을 수행한다.
        """
        if not settings.enable_secondary_intent:
            return self.route_by_intent(state)

        secondary = state["plan"].get("secondary_intent")
        primary = state["plan"].get("intent")
        if secondary is not None and primary in (
            IntentType.SQL_SEARCH,
            IntentType.VECTOR_SEARCH,
        ):
            return ["sql_node", "vector_node"]

        return self.route_by_intent(state)

    def post_cache_check(self, state: AgentState) -> str:
        """cache_check 직후 라우팅 — hit 시 search_persist_node → trace 경로, miss면 intent 분기.

        cache hit 시 검색이 수행되지 않으므로 search_channels 는 {} 상태다.
        search_persist_node 는 빈 채널 맵에서 즉시 skip 하고 return {} 하므로
        성능 오버헤드는 없다. 명시적으로 경유함으로써 종단 체인
        (cache_write → search_persist → trace) 의 일관성을 유지한다.

        NOTE: 직접 trace_node 로 라우팅하면 나중에 cache-hit 경로에서도 채널 데이터가
        존재하는 케이스가 생길 때 search_persist 가 묵묵히 스킵되는 latent bug 가 된다.
        """
        if state.get("cache_hit"):
            return "search_persist_node"
        return self.route_by_intent(state)

    def route_by_intent(self, state: AgentState) -> str:
        """intent 값에 따라 다음 노드를 결정한다."""
        error = state.get("error")
        answer = state["output"].get("answer") or ""

        # router_node 예외 시 fallback_answer + error가 모두 설정됨.
        # intent가 None이므로 아래 else 분기가 동일하게 처리하지만, 의도 명시용 early-return.
        if error and answer.strip():
            return "answer_node"

        intent = state["plan"].get("intent")
        if intent == IntentType.SQL_SEARCH:
            return "sql_node"
        elif intent == IntentType.VECTOR_SEARCH:
            return "vector_node"
        elif intent == IntentType.MAP:
            return "map_node"
        elif intent == IntentType.ANALYTICS:
            return "analytics_node"
        else:
            return "answer_node"
