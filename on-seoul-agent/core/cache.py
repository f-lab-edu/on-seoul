"""Answer Cache — refined_query 기반 전역 캐싱.

키:   answer_cache:{sha256(refined_query)[:16]}
값:   JSON {payload, state}
TTL:  정상 결과는 settings.answer_cache_ttl,
      빈 검색 결과(vector/sql 모두 empty)는 settings.answer_cache_empty_ttl

Redis 장애 시 fail-open. MAP/FALLBACK 및 error는 호출 측에서 가드한다.
"""

import hashlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "answer_cache:"


def _cache_key(
    query: str,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
) -> str:
    """합성 키 — refined_query + post-filter 조합.

    Router LLM이 prompt를 어기고 메타데이터를 분리 산출해도 사용자 간
    잘못된 cache hit이 발생하지 않도록, post-filter 필드를 키에 포함한다.
    None과 "" 는 동등하게 취급한다.
    """
    parts = [
        query.strip().lower(),
        f"max={max_class_name or ''}",
        f"area={area_name or ''}",
        f"status={service_status or ''}",
    ]
    composite = "|".join(parts)
    # 64-bit(16 hex) 잘라내기 — 동시 키 규모 O(1000)에서 충돌 확률 무시 가능.
    # 규모 확장 시 32자(128-bit)로 늘릴 것.
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()[:16]
    return f"{_KEY_PREFIX}{digest}"


def _is_empty_state(state: dict[str, Any]) -> bool:
    return not state.get("vector_results") and not state.get("sql_results")


async def get_cached_answer(
    query: str,
    redis: aioredis.Redis,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
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
