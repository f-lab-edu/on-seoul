"""비동기 Redis 클라이언트 팩토리.

MVP 단계에서는 설정 확인 및 연결 준비만 제공한다.
실제 캐싱/Rate Limiting은 Phase 14 이후에 활성화한다.
"""

import redis.asyncio as aioredis

from core.config import settings


def get_redis() -> aioredis.Redis:
    """settings.redis_url을 사용하는 비동기 Redis 클라이언트를 반환한다.

    호출 측에서 `async with` 또는 `await client.aclose()`로 생명주기를 관리한다.
    """
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
