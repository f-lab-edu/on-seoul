"""Answer Cache 노드 (router 직후 / answer 직후)."""

import logging
from typing import Any

from agents import _redis_gateway
from core.config import settings
from schemas.state import ActionType, AgentState, IntentType

logger = logging.getLogger(__name__)


class CacheCheckNode:
    """router 직후 — intent가 캐싱 대상이면 refined_query 기반으로 cache 조회.

    hit이면 state에 payload + 검색 결과 envelope를 복원하여 cache_hit=True로 표시한다.
    이후 라우팅은 graph 측 conditional edge에서 cache_hit으로 END 분기를 선택한다.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    @staticmethod
    def _build_routes_key(
        primary: IntentType | None, secondary: IntentType | None
    ) -> str | None:
        """primary + secondary intent를 정렬된 캐시 키 문자열로 변환한다."""
        if primary is None:
            return None
        parts = sorted(
            {primary.value} | ({secondary.value} if secondary is not None else set())
        )
        return ",".join(parts)

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        # 비-RETRIEVE action은 캐시 제외
        action = state["triage"].get("action")
        if action is not None and action != ActionType.RETRIEVE:
            return {"cache_hit": False}

        # 재시도 재진입 가드 — answer_lock_key 가 이미 있으면(최초 패스에서 기록)
        # 이 패스는 락 보유자로서 K_original 에 저장할 책임을 이미 확정한 상태다.
        # self-correction 재시도가 filters/intent 를 완화(K_relaxed)해도 여기서
        # 완화된 plan 으로 키를 재계산·재획득하지 않는다: 그러면 저장 키(K_original)와
        # 갈려 (1) 동일 원 질의가 계속 miss, (2) K_original 을 폴링하던 singleflight
        # 대기자가 고아가 된다. 저장 타깃 = 사용자 원 질의가 최초에 산출하는 키로
        # 고정하기 위해 재진입 시 슬롯을 보존하고 즉시 pass-through(miss)한다.
        # (락은 최초 획득 시점부터 cache_write 저장까지 K_original 에 걸린 채 유지된다.)
        if state.get("answer_lock_key"):
            return {"cache_hit": False}

        plan = state["plan"]
        intent = plan.get("intent")
        refined = plan.get("refined_query")
        if intent is None or refined is None:
            return {"cache_hit": False}
        if intent.value not in settings.answer_cache_eligible_intents:
            return {"cache_hit": False}

        filters = state["filters"]
        max_class_name = filters.get("max_class_name")
        area_name = filters.get("area_name")
        service_status = filters.get("service_status")
        payment_type = filters.get("payment_type")
        routes = self._build_routes_key(intent, plan.get("secondary_intent"))

        key = _redis_gateway.build_answer_cache_key(
            refined,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
            routes=routes,
        )

        # 이 패스가 락 보유자가 될 때만 키를 기록한다(해제 책임 추적). hit/poll-hit/
        # poll-timeout 경로는 락을 들지 않으므로 None 으로 둔다.
        lock_key: str | None = None

        envelope = await _redis_gateway.get_cached_answer_by_key(key, self._redis)
        if envelope is None:
            # singleflight: 첫 miss 호출자만 LLM 실행, 나머지는 결과 대기.
            acquired = await _redis_gateway.acquire_answer_lock(
                key, self._redis, ttl=settings.answer_cache_lock_ttl
            )
            if acquired:
                # 이 패스가 락 보유자 — 획득 시점 키(K_original)를 state 에 기록한다.
                # 이 키는 (1) singleflight 락 해제 대상이자 (2) cache_write 의 저장
                # 타깃(= 사용자 원 질의가 다음에 조회할 키)을 겸한다. 재시도 재진입 시
                # 위 answer_lock_key 가드가 이 슬롯을 보존하므로, 완화(K_relaxed)가 있어도
                # 락은 K_original 에 걸린 채 유지되고 저장도 K_original 로 이뤄진다.
                # 해제는 cache_write 가 단독으로 수행한다(retry_prep 는 더 이상 해제 안 함).
                lock_key = key
            if not acquired:
                logger.info(
                    "cache.singleflight.wait room=%s intent=%s refined=%r",
                    state.get("room_id"),
                    intent.value,
                    refined[:40],
                )
                envelope = await _redis_gateway.poll_for_answer(
                    key,
                    self._redis,
                    retries=settings.answer_cache_lock_poll_retries,
                    interval=settings.answer_cache_lock_poll_interval,
                )
                if envelope is not None:
                    logger.info(
                        "cache.singleflight.hit room=%s intent=%s refined=%r",
                        state.get("room_id"),
                        intent.value,
                        refined[:40],
                    )
                else:
                    # fail-open: poll 타임아웃 → 각자 LLM 실행
                    logger.info(
                        "cache.singleflight.timeout room=%s intent=%s refined=%r",
                        state.get("room_id"),
                        intent.value,
                        refined[:40],
                    )
                    return {"cache_hit": False}

        if envelope is None:
            logger.info(
                "cache.miss room=%s intent=%s refined=%r",
                state.get("room_id"),
                intent.value,
                refined[:40],
            )
            # 락 보유자면 키를 넘겨 후속 노드(retry_prep/cache_write)가 해제한다.
            return {"cache_hit": False, "answer_lock_key": lock_key}

        payload = envelope.get("payload", {}) or {}
        snap = envelope.get("state", {}) or {}
        logger.info(
            "cache.hit room=%s intent=%s refined=%r",
            state.get("room_id"),
            intent.value,
            refined[:40],
        )
        return {
            "output": {
                "answer": payload.get("answer"),
                # title 은 별도 채널(generate_title_node)로 분리되어 answer 캐시에서
                # 더 이상 저장/복원하지 않는다.
                # service_cards 는 payload 에 저장된다 (답변 결과물, search snapshot 아님).
                # 구버전 envelope (키 미존재) 는 None 폴백 —
                # routers/chat.py final payload 직렬화 단의 `or []` 가
                # 빈 배열로 안전하게 노출한다.
                "service_cards": payload.get("service_cards"),
            },
            "vector": {"results": snap.get("vector_results")},
            "sql": {"results": snap.get("sql_results")},
            # hydrated_services 도 envelope 에 포함되어 있으면 복원한다.
            # 미보유 envelope(구버전 캐시 엔트리) 인 경우 None — AnswerAgent 가 폴백 처리.
            "hydration": {"hydrated_services": snap.get("hydrated_services")},
            "filters": {
                "max_class_name": snap.get("max_class_name"),
                "area_name": snap.get("area_name"),
                "service_status": snap.get("service_status"),
                "payment_type": snap.get("payment_type"),
            },
            "cache_hit": True,
        }


class CacheWriteNode:
    """answer 직후 — 정상 결과만 캐싱 (SQL_SEARCH / VECTOR_SEARCH).

    skip 조건: error / cache_hit / non-eligible intent / answer or refined 누락.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        if state.get("cache_hit"):
            return {}
        # 비-RETRIEVE action은 캐시 저장 제외
        action = state["triage"].get("action")
        if action is not None and action != ActionType.RETRIEVE:
            return {}
        plan = state["plan"]
        intent = plan.get("intent")
        if intent is None or intent.value not in settings.answer_cache_eligible_intents:
            return {}
        refined = plan.get("refined_query")
        answer = state["output"].get("answer")
        if not refined or not answer:
            return {}

        filters = state["filters"]
        max_class_name = filters.get("max_class_name")
        area_name = filters.get("area_name")
        service_status = filters.get("service_status")
        payment_type = filters.get("payment_type")
        routes = CacheCheckNode._build_routes_key(intent, plan.get("secondary_intent"))

        # 저장 키: 최초 cache_check 시점 키(K_original) 우선.
        # self-correction 재시도로 filters/intent 가 완화되면 현재 state 로 재계산한
        # 키(K_relaxed)는 사용자 원 질의가 다음에 산출하는 키와 갈린다. answer_lock_key
        # (= 최초 check 시점 키)로 저장해 (1) 동일 원 질의 재요청이 hit 하고,
        # (2) 그 키를 폴링하던 singleflight 대기자가 hit 하도록 정합을 맞춘다.
        # 없으면(구 경로/poll-timeout fail-open 으로 락 미보유) 재계산 키로 폴백해
        # 정상(비재시도) 경로 동작을 불변으로 유지한다(K_original == K_final).
        store_key = state.get("answer_lock_key") or _redis_gateway.build_answer_cache_key(
            refined,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
            routes=routes,
        )

        output = state["output"]
        payload = {
            "message_id": state.get("message_id"),
            "answer": answer,
            "intent": intent.value,
            # title 은 별도 채널(generate_title_node)로 분리되어 answer 캐시에서 제외.
            # 답변 결과물 — cache hit 시 프론트 카드 UI 가 다시 사용할 수 있도록 보존.
            # snap 이 아닌 payload 에 두는 이유: search snapshot 이 아니라 LLM 답변과 함께
            # 같은 라이프사이클로 묶이는 결과물이기 때문.
            "service_cards": output.get("service_cards"),
        }
        # snap 은 answer-cache envelope 의 평면 스냅샷 — AgentState 구조와 독립된
        # 캐시 계약이라 키명을 평면으로 유지한다(cache_check 복원과 round-trip).
        snap = {
            "refined_query": refined,
            "max_class_name": max_class_name,
            "area_name": area_name,
            "service_status": service_status,
            "payment_type": payment_type,
            "vector_results": state["vector"].get("results"),
            "sql_results": state["sql"].get("results"),
            # HydrationNode 가 채운 통합 슬롯 — cache hit 시 hydration 라운드트립 절감.
            "hydrated_services": state["hydration"].get("hydrated_services"),
        }
        await _redis_gateway.set_cached_answer_by_key(
            store_key,
            payload,
            snap,
            self._redis,
        )
        # singleflight 락 조기 해제 — waiter가 poll 주기를 기다리지 않고 즉시 hit.
        # 저장 키(store_key = answer_lock_key 우선)와 동일 키로 해제해 획득·저장·해제가
        # 모두 K_original 에 정합된다(락 키 ↔ 저장 키 일치 → 대기자 hit). retry_prep 는
        # 더 이상 락을 해제하지 않으므로(락은 전 요청 수명 유지) cache_write 가 단독 해제한다.
        await _redis_gateway.release_answer_lock(store_key, self._redis)
        empty = not snap["vector_results"] and not snap["sql_results"]
        logger.info("cache.write intent=%s empty=%s", intent.value, empty)
        return {"answer_lock_key": None}
