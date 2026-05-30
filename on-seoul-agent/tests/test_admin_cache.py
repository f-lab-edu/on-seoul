"""POST /admin/cache/flush 라우터 테스트.

X-Internal-Token 헤더 보호와 flush_answer_cache 호출을 검증한다.
실제 Redis 연결 없이 mock으로 처리한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routers import admin as admin_router


_SENTINEL = object()


def _build_app(redis_mock: object | None = _SENTINEL) -> FastAPI:
    """테스트용 FastAPI 앱.

    - redis_mock=_SENTINEL (기본): app.state.redis = MagicMock() 자동 세팅
    - redis_mock=None: app.state.redis를 세팅하지 않음 (503 시나리오)
    - 그 외: 주어진 객체를 app.state.redis로 세팅
    """
    app = FastAPI()
    app.include_router(admin_router.router)
    if redis_mock is _SENTINEL:
        app.state.redis = MagicMock()
    elif redis_mock is not None:
        app.state.redis = redis_mock
    # redis_mock is None인 경우 세팅하지 않음
    return app


@pytest.mark.asyncio
async def test_unauthorized_without_token():
    """X-Internal-Token 헤더 누락 시 401."""
    app = _build_app()
    with patch("routers.admin.settings") as s:
        s.admin_internal_token = "secret-token"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.post("/admin/cache/flush")
    assert res.status_code == 401
    assert res.json()["detail"] == "invalid token"


@pytest.mark.asyncio
async def test_wrong_token_rejected():
    """잘못된 토큰 401."""
    app = _build_app()
    with patch("routers.admin.settings") as s:
        s.admin_internal_token = "secret-token"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "wrong"}
            )
    assert res.status_code == 401
    assert res.json()["detail"] == "invalid token"


@pytest.mark.asyncio
async def test_authorized_flush_returns_count():
    """정확한 토큰 + flush mock → 200, {"deleted": N}."""
    app = _build_app()
    flush_mock = AsyncMock(return_value=42)
    with (
        patch("routers.admin.settings") as s,
        patch("routers.admin.flush_answer_cache", flush_mock),
    ):
        s.admin_internal_token = "secret-token"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.post(
                "/admin/cache/flush",
                headers={"X-Internal-Token": "secret-token"},
            )
    assert res.status_code == 200
    assert res.json() == {"deleted": 42}
    flush_mock.assert_awaited_once()
    # flush_answer_cache는 app.state.redis를 인자로 받아야 한다
    called_args, _ = flush_mock.call_args
    assert called_args[0] is app.state.redis


@pytest.mark.asyncio
async def test_empty_configured_token_rejects_all():
    """admin_internal_token이 빈 문자열이면 어떤 헤더도 401 (오설정 보호)."""
    app = _build_app()
    with patch("routers.admin.settings") as s:
        s.admin_internal_token = ""
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 빈 헤더
            res1 = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": ""}
            )
            # 헤더 누락
            res2 = await client.post("/admin/cache/flush")
            # 임의 토큰
            res3 = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "anything"}
            )
    assert res1.status_code == 401
    assert res2.status_code == 401
    assert res3.status_code == 401
    for res in (res1, res2, res3):
        assert res.json()["detail"] == "admin disabled"


@pytest.mark.asyncio
async def test_get_method_not_allowed():
    """GET 등 다른 메서드는 405."""
    app = _build_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        res = await client.get("/admin/cache/flush")
    assert res.status_code == 405


@pytest.mark.asyncio
async def test_flush_zero_when_no_keys():
    """삭제할 키가 없을 때 deleted=0을 그대로 반환한다 (fail-open 회귀)."""
    app = _build_app()
    flush_mock = AsyncMock(return_value=0)
    with (
        patch("routers.admin.settings") as s,
        patch("routers.admin.flush_answer_cache", flush_mock),
    ):
        s.admin_internal_token = "tok"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "tok"}
            )
    assert res.status_code == 200
    assert res.json() == {"deleted": 0}


@pytest.mark.asyncio
async def test_redis_not_in_app_state_returns_503():
    """app.state.redis가 미초기화면 503 (lifespan 강제 정책)."""
    app = _build_app(redis_mock=None)  # app.state.redis 세팅 안 함
    with patch("routers.admin.settings") as s:
        s.admin_internal_token = "tok"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            res = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "tok"}
            )
    assert res.status_code == 503
    assert res.json()["detail"] == "redis unavailable"


@pytest.mark.asyncio
async def test_compare_digest_handles_different_length_tokens():
    """길이가 다른 토큰도 예외 없이 401로 거부된다 (compare_digest 안전성)."""
    app = _build_app()
    with patch("routers.admin.settings") as s:
        s.admin_internal_token = "secret-token-long"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # 더 짧은 토큰
            res_short = await client.post(
                "/admin/cache/flush", headers={"X-Internal-Token": "short"}
            )
            # 더 긴 토큰
            res_long = await client.post(
                "/admin/cache/flush",
                headers={"X-Internal-Token": "secret-token-long-extra-suffix"},
            )
    assert res_short.status_code == 401
    assert res_short.json()["detail"] == "invalid token"
    assert res_long.status_code == 401
    assert res_long.json()["detail"] == "invalid token"
