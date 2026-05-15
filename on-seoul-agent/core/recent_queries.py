"""per-room 최근 질의 큐 — Router Agent의 follow-up 컨텍스트.

저장값은 사용자 원본 message만 포함 (answer/intent 제외).
LIST 최신 인덱스 0. LPUSH + LTRIM + EXPIRE.
"""

import logging

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)


def _key(room_id: int) -> str:
    return f"recent_queries:room:{room_id}"


async def get_recent_queries(room_id: int, redis: aioredis.Redis) -> list[str]:
    """최신 순으로 최근 질의를 반환. 비활성/장애 시 빈 리스트."""
    if not settings.recent_queries_enabled:
        return []
    try:
        items = await redis.lrange(_key(room_id), 0, settings.recent_queries_max - 1)
        # get_redis()가 decode_responses=True로 생성하므로 항목은 항상 str이다.
        # bytes 분기는 클라이언트 설정 변경에 대한 방어 코드.
        return [item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in items]
    except Exception:
        logger.warning("recent_queries GET 오류 — 빈 컨텍스트", exc_info=True)
        return []


async def push_recent_query(room_id: int, message: str, redis: aioredis.Redis) -> None:
    """원본 message를 큐에 push. 장애 시 무시."""
    if not settings.recent_queries_enabled:
        return
    if not message or not message.strip():
        return
    key = _key(room_id)
    try:
        await redis.lpush(key, message.strip())
        await redis.ltrim(key, 0, settings.recent_queries_max - 1)
        await redis.expire(key, settings.recent_queries_ttl)
    except Exception:
        logger.warning("recent_queries PUSH 오류 — 컨텍스트 누락", exc_info=True)
