"""내부 admin 엔드포인트 — Spring Boot 수집 스케줄러용.

보호: X-Internal-Token 헤더가 settings.admin_internal_token과 일치해야 한다.
빈 토큰 설정 시 모든 요청 거부 (오설정 시 노출 방지).
"""

import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from core.cache import flush_answer_cache
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _verify_token(x_internal_token: str | None = Header(default=None)) -> None:
    """X-Internal-Token 헤더 검증.

    - settings.admin_internal_token이 빈 값이면 모든 요청 거부 (오설정 보호).
    - 헤더가 없거나 토큰이 일치하지 않으면 401.
    - 토큰 비교는 secrets.compare_digest로 타이밍 공격을 방지한다.
    토큰 값 자체는 로그에 남기지 않는다.
    """
    expected = settings.admin_internal_token
    if not expected:
        logger.warning("admin 엔드포인트 호출 거부 — admin_internal_token 미설정")
        raise HTTPException(status_code=401, detail="admin disabled")
    if x_internal_token is None or not secrets.compare_digest(
        x_internal_token, expected
    ):
        logger.warning("admin 엔드포인트 호출 거부 — invalid token")
        raise HTTPException(status_code=401, detail="invalid token")


def _get_redis(request: Request) -> Any:
    """app.state.redis를 반환. lifespan에서 세팅되지 않았다면 503."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        logger.error("admin 엔드포인트 호출 거부 — app.state.redis 미초기화")
        raise HTTPException(status_code=503, detail="redis unavailable")
    return redis


@router.post("/cache/flush", dependencies=[Depends(_verify_token)])
async def cache_flush(
    request: Request, redis: Any = Depends(_get_redis)
) -> dict[str, int]:
    """Answer Cache(`answer_cache:*`) 전체 삭제.

    Spring Boot 수집 스케줄러가 데이터 갱신 후 호출한다.
    응답에는 삭제 개수만 포함하며 키 자체는 노출하지 않는다.
    """
    deleted = await flush_answer_cache(redis)
    logger.info("answer cache flush 완료 — deleted=%d", deleted)
    return {"deleted": deleted}
