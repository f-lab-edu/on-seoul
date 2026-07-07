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
  해제:  CacheWriteNode가 저장(set_cached_answer_by_key) 완료 후 DEL → 대기자 즉시 hit.
         락은 최초 cache_check 획득 시점부터 cache_write 저장까지 K_original 에 유지되며
         (self-correction 재시도 재진입에서도 재획득·해제하지 않는다), cache_write 가
         K_original(= answer_lock_key)로 저장·해제해 저장 키 ↔ 락 키 정합을 맞춘다.
  fail-open: Redis 장애 또는 poll 윈도우(retries×interval) 초과 → 각자 LLM 호출
             (중복 허용, 안전한 퇴행).
  폴 윈도우: poll_window(≈p95 답변 생성시간, ~10s) < lock_ttl(worst-case 30s).
             보유자는 답변 *완료* 시점(스트리밍)에 캐시를 쓰고 락을 DEL 하므로,
             waiter 는 보유자의 답변 생성시간만큼 폴해야 hit 한다. config.py 의
             answer_cache_lock_* 주석에 불변식·튜닝 근거를 둔다.

  키 ↔ singleflight 관계 (어떤 경우에 실제로 중복을 막는가):
    - 락 키 = answer 캐시 키 = sha256(refined_query + post-filter + routes).
      따라서 **동시 요청들이 동일 refined_query+filters+routes 를 산출할 때만**
      singleflight 가 중복을 막는다.
    - herd 가 실제 문제되는 case(같은 first-turn 질의, history 없음)는 refine LLM 이
      temperature=0(get_chat_model 기본)이라 동일 refined_query → 동일 키 →
      락 충돌 → singleflight 작동.
    - follow-up(대화별 history 상이)은 키가 갈리는데, 이는 맥락이 다른 답을
      공유하지 않기 위한 의도된 동작이다(정상, herd 아님).
    - caveat: 폴 연장은 "보유자가 waiter 와 같은 키로 캐시를 쓴다"는 전제에서만 hit 으로
      이어진다. refine 비결정성으로 키가 갈리면 그 waiter 는 폴만 길어지고
      fail-open 한다(영향 작음 — 각자 LLM 실행).

Refine Singleflight (answer singleflight 대칭):
  락 키: refine_cache:{version}:{digest}:lock  (refine 캐시 키 + ":lock" 접미사)
  획득:  SET NX EX refine_cache_lock_ttl — True면 이 호출자가 refine LLM 을 실행,
         False면 대기.
  대기:  miss + 락 미획득 → poll_for_refine()로 refine 캐시 키를 retries×interval 초
         주기 재조회. envelope 는 get_cached_refine 과 동일한 평면 dict 형태로 복원.
  해제:  router_node 가 set_cached_refine 완료 후 release_refine_lock 으로 DEL
         (try/finally — 성공·예외 모두 해제, 락 누수 금지) → 대기자 즉시 hit.
  fail-open: Redis 장애(acquire 예외) 또는 poll 윈도우 초과 → 각자 refine LLM 호출.
             refine 은 temperature=0 결정론이라 중복 결과가 동일 → last-write-wins
             정합 trivially 안전.
  폴 윈도우: refine LLM 은 ~0.5s 로 answer(~10s)보다 훨씬 빠르므로 answer 와 별도
             노브(refine_cache_lock_*)를 둔다. 불변식 poll_window(2s) < lock_ttl(10s).
  공통 헬퍼: answer/refine 의 SET NX / DEL / poll 내부 로직은 _acquire_lock /
             _release_lock / _poll_for_cache 로 추출했다. answer 경로 동작은
             불변(토글·TTL·poll 설정만 호출 측에서 주입).
  forced_intent 경로: retry_prep→router 재진입 시 forced_intent 가 있으면 classify 를
             skip 하므로 락을 획득하지 않는다(release 도 미수행 — 없는 락 DEL 은 무해하나
             애초에 acquire 를 건너뜀). retry 2회차엔 refine_cache 가 1회차로 채워져
             refine_cache_hit 으로 빠지므로 락 경로 미진입이 정상.

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


def _multi_key(value: "list[str] | str | None") -> str:
    """멀티값 필터(list) 를 정렬·조인해 순서 무관 안정 키로 만든다.

    max_class_name/area_name 은 list[str] 로 넘어온다. 정렬해 조인하면 추출 순서가
    달라도 동일 키가 나온다(추출이 이미 결정적 순서지만 방어). 스칼라/None 은 하위호환.
    """
    if isinstance(value, list):
        return ",".join(sorted(value))
    return value or ""


def _cache_key(
    query: str,
    *,
    max_class_name: "list[str] | None" = None,
    area_name: "list[str] | None" = None,
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
        f"max={_multi_key(max_class_name)}",
        f"area={_multi_key(area_name)}",
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
    max_class_name: "list[str] | None" = None,
    area_name: "list[str] | None" = None,
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


async def _acquire_lock(key: str, redis: aioredis.Redis, *, ttl: int) -> bool:
    """공통 singleflight 락 획득 — SET NX EX. (answer/refine 공유 로직.)

    토글 가드는 호출 측 얇은 래퍼가 담당한다(노브가 answer/refine 별개라서).
    Redis 장애 시 True(fail-open): 락 없이 각자 진행해 중복 LLM 이 발생하나
    결과 정합성은 유지된다(last-write-wins).
    """
    try:
        result = await redis.set(f"{key}{_LOCK_SUFFIX}", "1", nx=True, ex=ttl)
        return bool(result)
    except Exception:
        logger.warning("singleflight lock 획득 오류 — fail-open(True)", exc_info=True)
        return True


async def _release_lock(key: str, redis: aioredis.Redis) -> None:
    """공통 singleflight 락 해제 — DEL. 실패 시 무시(TTL 자동 만료)."""
    try:
        await redis.delete(f"{key}{_LOCK_SUFFIX}")
    except Exception:
        pass


async def _poll_for_cache(
    key: str, redis: aioredis.Redis, *, retries: int, interval: float
) -> dict | None:
    """공통 waiter 폴 — 락 보유자가 캐시를 채울 때까지 주기 재조회.

    retries × interval 초 후에도 결과가 없으면 None(fail-open).
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


async def acquire_answer_lock(
    key: str, redis: aioredis.Redis, *, ttl: int
) -> bool:
    """answer 캐시 키에 대한 분산 singleflight 락 획득 시도.

    SET NX EX ttl — 성공(True)이면 이 호출자가 LLM을 실행해야 한다.
    실패(False)이면 다른 호출자가 이미 진행 중 → poll_for_answer()로 결과 대기.
    Redis 장애 시 True(fail-open).
    """
    if not settings.answer_cache_singleflight_enabled:
        return True
    return await _acquire_lock(key, redis, ttl=ttl)


async def release_answer_lock(key: str, redis: aioredis.Redis) -> None:
    """answer singleflight 락 조기 해제.

    CacheWriteNode가 캐시 쓰기 직후 호출 → 대기 중인 waiter가 즉시 hit.
    DEL 실패 시 무시(lock은 TTL에 의해 자동 만료됨).
    """
    if not settings.answer_cache_singleflight_enabled:
        return
    await _release_lock(key, redis)


async def poll_for_answer(
    key: str, redis: aioredis.Redis, *, retries: int, interval: float
) -> dict | None:
    """락 보유자가 answer 캐시를 채울 때까지 주기적으로 재조회(waiter 전용).

    retries × interval 초 대기 후에도 결과가 없으면 None 반환(fail-open).
    """
    return await _poll_for_cache(key, redis, retries=retries, interval=interval)


async def acquire_refine_lock(
    key: str, redis: aioredis.Redis, *, ttl: int
) -> bool:
    """refine 캐시 키에 대한 분산 singleflight 락 획득 시도(answer 대칭).

    SET NX EX ttl — True면 이 호출자가 refine LLM(classify)을 실행, False면 대기.
    refine 전용 토글(refine_cache_singleflight_enabled)로 게이트한다.
    비활성화 또는 Redis 장애 시 True(fail-open).
    """
    if not settings.refine_cache_singleflight_enabled:
        return True
    return await _acquire_lock(key, redis, ttl=ttl)


async def release_refine_lock(key: str, redis: aioredis.Redis) -> None:
    """refine singleflight 락 조기 해제(answer 대칭).

    router_node 가 set_cached_refine 직후 try/finally 로 호출 → waiter 즉시 hit.
    DEL 실패 시 무시(TTL 자동 만료).
    """
    if not settings.refine_cache_singleflight_enabled:
        return
    await _release_lock(key, redis)


async def poll_for_refine(
    key: str, redis: aioredis.Redis, *, retries: int, interval: float
) -> dict | None:
    """락 보유자가 refine 캐시를 채울 때까지 주기적으로 재조회(waiter 전용).

    refine 캐시 키를 재조회하고 get_cached_refine 과 동일한 평면 dict 를 반환한다.
    retries × interval 초 후에도 결과가 없으면 None(fail-open).
    """
    return await _poll_for_cache(key, redis, retries=retries, interval=interval)


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


def build_refine_cache_key(query: str, history: list[dict[str, str]] | None) -> str:
    """refine cache 키를 외부에서 미리 계산할 수 있도록 공개 래퍼 제공.

    router_node 가 동일 키로 GET·lock·poll·SET·unlock 을 수행하기 위해 키를 한 번만
    계산해 재사용한다(build_answer_cache_key 와 대칭).
    """
    return _refine_cache_key(query, history)


async def get_cached_refine_by_key(key: str, redis: aioredis.Redis) -> dict | None:
    """미리 계산된 키로 refine cache 조회(answer get_cached_answer_by_key 대칭).

    poll_for_refine 의 hit 복원이 get_cached_refine 과 동일한 평면 dict 를 돌려주는
    것과 round-trip 정합. miss/장애/비활성화 시 None.
    """
    if not settings.refine_cache_enabled:
        return None
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        logger.warning("refine cache GET 오류(by_key) — miss 처리", exc_info=True)
        return None


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


async def set_cached_answer_by_key(
    key: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    redis: aioredis.Redis,
) -> None:
    """미리 계산된 키로 envelope({payload, state})를 저장(set_cached_answer 대칭).

    set_cached_answer 는 refined_query+filters+routes 로 키를 *재계산*하지만, 이 변형은
    호출 측이 미리 계산해 둔 키(= 최초 cache_check 시점 키)에 그대로 저장한다.
    self-correction 재시도로 filters/intent 가 완화(K_relaxed)되어도 저장은 사용자
    원 질의가 다음에 조회할 키(K_original)로 이뤄져, 동일 질의 재요청이 hit 한다.
    empty-state TTL 로직(빈 검색 결과 → answer_cache_empty_ttl)은 보존한다.
    장애 시 무시(get_cached_answer_by_key 대칭 fail-open).
    """
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
            key,
            json.dumps(envelope, ensure_ascii=False, default=str),
            ex=ttl,
        )
    except Exception:
        logger.warning("answer cache SET 오류(by_key) — 캐싱 건너뜀", exc_info=True)


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
