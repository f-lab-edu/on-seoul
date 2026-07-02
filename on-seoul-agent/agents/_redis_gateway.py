"""Redis 캐시/락 게이트웨이 — core.cache 단일 진입점(B2-1).

노드가 core.cache 를 직접 import 하지 않게 하는 자원별 단일 진입점이다.
answer·refine 의 GET/SET·락·폴링·키빌드 13종을 재노출(import + __all__)한다.
로직 추가 없이 시그니처 위임만 한다 — redis 인자는 그대로 받아 위임(시그니처 불변).

테스트 patch 타깃: agents._redis_gateway.<동일명> (자원 단위 단일 이음매).
"""

from core.cache import (
    acquire_answer_lock,
    acquire_refine_lock,
    build_answer_cache_key,
    build_refine_cache_key,
    get_cached_answer_by_key,
    get_cached_refine_by_key,
    poll_for_answer,
    poll_for_refine,
    release_answer_lock,
    release_refine_lock,
    set_cached_answer,
    set_cached_answer_by_key,
    set_cached_refine,
)

__all__ = [
    "acquire_answer_lock",
    "acquire_refine_lock",
    "build_answer_cache_key",
    "build_refine_cache_key",
    "get_cached_answer_by_key",
    "get_cached_refine_by_key",
    "poll_for_answer",
    "poll_for_refine",
    "release_answer_lock",
    "release_refine_lock",
    "set_cached_answer",
    "set_cached_answer_by_key",
    "set_cached_refine",
]
