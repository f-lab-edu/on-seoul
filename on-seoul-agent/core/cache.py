"""Answer Cache + Refine Cache — 전역 Redis 결과 캐싱.

Answer Cache:
  키:   answer_cache:{sha256(refined_query+filters+routes)[:32]}  (128-bit)
  값:   JSON {payload, state}
  TTL:  정상 결과는 settings.answer_cache_ttl,
        빈 검색 결과(vector/sql 모두 empty)는 settings.answer_cache_empty_ttl
  flush: /admin/cache/flush 대상(수집 시 데이터 무효화).

Refine Cache (0-3-3):
  키:   refine_cache:{version}:{sha256(norm_query[+history_hash])[:32]}  (128-bit)
  값:   JSON {intent, refined_query, max_class_name, area_name, service_status,
              payment_type, vector_sub_intent, secondary_intent}
  TTL:  settings.refine_cache_ttl (장기 — 데이터 비의존).
  flush: 비대상(데이터 비의존이라 수집 무효화 불필요, 네임스페이스도 분리됨).

Singleflight (0-3-2):
  락 키: answer_cache:{digest}:lock  (캐시 키 + ":lock" 접미사)
  획득:  SET NX EX lock_ttl — True면 이 호출자가 락 보유, False면 대기.
  대기:  miss + 락 미획득 → poll_for_answer()로 캐시 키를 retries×interval 초 주기 재조회.
  해제:  CacheWriteNode가 set_cached_answer 완료 후 DEL → 대기자 즉시 hit.
  fail-open: Redis 장애 또는 poll 타임아웃 → 각자 LLM 호출(중복 허용, 안전한 퇴행).

Redis 장애 시 fail-open. MAP/FALLBACK 및 error는 호출 측에서 가드한다.
"""

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "answer_cache:"
_LOCK_SUFFIX = ":lock"
_REFINE_KEY_PREFIX = "refine_cache:"
# refine 캐시 키 버전. 라우터 프롬프트/모델/분류 화이트리스트를 무중단 핫 변경하면
# 구 매핑이 장수명 TTL(refine_cache_ttl) 동안 잔존하는데, flush_answer_cache 는
# answer_cache:* 만 비우므로 refine 은 무효화되지 않는다. 그런 변경 시 이 상수만
# bump 하면 키 네임스페이스가 갈라져 기존 refine 캐시 전체가 즉시 무효화된다.
# (answer 캐시는 데이터 의존 → flush 로 무효화하므로 버전 prefix 불필요.)
_REFINE_CACHE_VERSION = "v1"
# digest 길이(hex). 128-bit(32 hex) — 동시 키 규모 O(1000)에서 충돌 확률 무시 가능하며
# 64-bit 대비 생일 역설 충돌 여유 대폭 확대. answer/refine 양 캐시 키에 공통 적용.
_DIGEST_HEX_LEN = 32

_WS_RE = re.compile(r"\s+")


def _cache_key(
    query: str,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    routes: str | None = None,
) -> str:
    """합성 키 — refined_query + post-filter + routes 조합.

    Router LLM이 prompt를 어기고 메타데이터를 분리 산출해도 사용자 간
    잘못된 cache hit이 발생하지 않도록, post-filter 필드를 키에 포함한다.
    None과 "" 는 동등하게 취급한다.

    routes: primary+secondary intent를 정렬된 집합 문자열로 표현
            (예: "SQL_SEARCH" 또는 "SQL_SEARCH,VECTOR_SEARCH").
            단일 라우트 또는 secondary 없는 경우는 primary intent 문자열.
    """
    parts = [
        query.strip().lower(),
        f"max={max_class_name or ''}",
        f"area={area_name or ''}",
        f"status={service_status or ''}",
        f"pay={payment_type or ''}",
        f"routes={routes or ''}",
    ]
    composite = "|".join(parts)
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:_DIGEST_HEX_LEN]
    return f"{_KEY_PREFIX}{digest}"


def build_answer_cache_key(
    query: str,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    routes: str | None = None,
) -> str:
    """answer cache 키를 외부에서 미리 계산할 수 있도록 공개 래퍼 제공.

    CacheCheckNode / CacheWriteNode가 동일 키로 GET·lock·SET·unlock을 수행하기
    위해 키를 한 번만 계산해 재사용한다.
    """
    return _cache_key(
        query,
        max_class_name=max_class_name,
        area_name=area_name,
        service_status=service_status,
        payment_type=payment_type,
        routes=routes,
    )


async def get_cached_answer_by_key(key: str, redis: aioredis.Redis) -> dict | None:
    """미리 계산된 키로 answer cache 조회. miss/장애 시 None."""
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("answer cache GET 오류(by_key) — miss 처리", exc_info=True)
        return None


async def acquire_answer_lock(
    key: str, redis: aioredis.Redis, *, ttl: int
) -> bool:
    """캐시 키에 대한 분산 singleflight 락 획득 시도.

    SET NX EX ttl — 성공(True)이면 이 호출자가 LLM을 실행해야 한다.
    실패(False)이면 다른 호출자가 이미 진행 중 → poll_for_answer()로 결과 대기.
    Redis 장애 시 True(fail-open): 락 없이 각자 진행해 중복 LLM이 발생하지만
    결과의 정합성은 유지된다(last-write-wins).
    """
    if not settings.answer_cache_singleflight_enabled:
        return True
    try:
        result = await redis.set(f"{key}{_LOCK_SUFFIX}", "1", nx=True, ex=ttl)
        return bool(result)
    except Exception:
        logger.warning("answer cache lock 획득 오류 — fail-open(True)", exc_info=True)
        return True


async def release_answer_lock(key: str, redis: aioredis.Redis) -> None:
    """singleflight 락 조기 해제.

    CacheWriteNode가 캐시 쓰기 직후 호출 → 대기 중인 waiter가 즉시 hit.
    DEL 실패 시 무시(lock은 TTL에 의해 자동 만료됨).
    """
    if not settings.answer_cache_singleflight_enabled:
        return
    try:
        await redis.delete(f"{key}{_LOCK_SUFFIX}")
    except Exception:
        pass


async def poll_for_answer(
    key: str, redis: aioredis.Redis, *, retries: int, interval: float
) -> dict | None:
    """락 보유자가 캐시를 채울 때까지 주기적으로 재조회(waiter 전용).

    retries × interval 초 대기 후에도 결과가 없으면 None 반환(fail-open).
    """
    for _ in range(retries):
        await asyncio.sleep(interval)
        try:
            raw = await redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            return None
    return None


def _normalize_query(query: str) -> str:
    """raw query 정규화 — strip + 공백 collapse + 소문자. (과한 정규화 지양.)"""
    return _WS_RE.sub(" ", query.strip()).lower()


def _refine_cache_key(query: str, history: list[dict[str, str]] | None) -> str:
    """refine 캐시 키 — 정규화 raw query (+ history 있으면 history 해시).

    refine 출력 = f(raw_query, history) (router_node 가 history 를 프롬프트에 합성).
    history 가 없으면 키에서 history 부분을 생략 → first-turn 사용자 간 공유(적중 최대).
    """
    norm = _normalize_query(query)
    if history:
        # role+content 를 순서 보존 직렬화 후 해시 — 동일 history 는 동일 해시.
        hist_repr = json.dumps(history, ensure_ascii=False, sort_keys=False)
        hist_hash = hashlib.sha256(hist_repr.encode("utf-8")).hexdigest()[
            :_DIGEST_HEX_LEN
        ]
        composite = f"{norm}|h={hist_hash}"
    else:
        composite = norm
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:_DIGEST_HEX_LEN]
    return f"{_REFINE_KEY_PREFIX}{_REFINE_CACHE_VERSION}:{digest}"


async def get_cached_refine(
    query: str,
    history: list[dict[str, str]] | None,
    redis: aioredis.Redis,
) -> dict | None:
    """캐시된 refine 출력(dict)을 반환. miss/장애 시 None."""
    if not settings.refine_cache_enabled:
        return None
    try:
        raw = await redis.get(_refine_cache_key(query, history))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("refine cache GET 오류 — miss 처리", exc_info=True)
        return None


async def set_cached_refine(
    query: str,
    history: list[dict[str, str]] | None,
    value: dict[str, Any],
    redis: aioredis.Redis,
) -> None:
    """refine 출력(dict)을 저장. 장애 시 무시. 데이터 비의존이라 장기 TTL."""
    if not settings.refine_cache_enabled:
        return
    try:
        await redis.set(
            _refine_cache_key(query, history),
            json.dumps(value, ensure_ascii=False, default=str),
            ex=settings.refine_cache_ttl,
        )
    except Exception:
        logger.warning("refine cache SET 오류 — 캐싱 건너뜀", exc_info=True)


def _is_empty_state(state: dict[str, Any]) -> bool:
    return not state.get("vector_results") and not state.get("sql_results")


async def get_cached_answer(
    query: str,
    redis: aioredis.Redis,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    routes: str | None = None,
) -> dict | None:
    """캐시된 envelope({payload, state})를 반환. miss/장애 시 None."""
    if not settings.answer_cache_enabled:
        return None
    try:
        raw = await redis.get(
            _cache_key(
                query,
                max_class_name=max_class_name,
                area_name=area_name,
                service_status=service_status,
                payment_type=payment_type,
                routes=routes,
            )
        )
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("answer cache GET 오류 — miss 처리", exc_info=True)
        return None


async def set_cached_answer(
    query: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    redis: aioredis.Redis,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    routes: str | None = None,
) -> None:
    """payload + state 일부를 envelope로 저장. 장애 시 무시."""
    if not settings.answer_cache_enabled:
        return
    ttl = (
        settings.answer_cache_empty_ttl
        if _is_empty_state(state)
        else settings.answer_cache_ttl
    )
    envelope = {"payload": payload, "state": state}
    try:
        await redis.set(
            _cache_key(
                query,
                max_class_name=max_class_name,
                area_name=area_name,
                service_status=service_status,
                payment_type=payment_type,
                routes=routes,
            ),
            json.dumps(envelope, ensure_ascii=False, default=str),
            ex=ttl,
        )
    except Exception:
        logger.warning("answer cache SET 오류 — 캐싱 건너뜀", exc_info=True)


async def flush_answer_cache(redis: aioredis.Redis) -> int:
    """`answer_cache:*` 키 전체 삭제. 삭제된 키 수 반환. 장애 시 0."""
    deleted = 0
    try:
        batch: list = []
        async for key in redis.scan_iter(match=f"{_KEY_PREFIX}*"):
            batch.append(key)
            if len(batch) >= 500:
                await redis.delete(*batch)
                deleted += len(batch)
                batch = []
        if batch:
            await redis.delete(*batch)
            deleted += len(batch)
        logger.info("cache.flush deleted=%d", deleted)
    except Exception:
        logger.warning(
            "answer cache flush 오류 (deleted=%d 반영 안 됨)", deleted, exc_info=True
        )
    return deleted
