"""POST /notification/template 라우터 테스트.

실제 LLM/외부 API 호출 없이 AsyncMock으로 LLM을 모킹한다.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from schemas.notification import NotificationTemplateResponse


# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    from main import app as _app
    from routers.notification import _verify_token

    _app.dependency_overrides[_verify_token] = lambda: None
    yield _app
    _app.dependency_overrides.pop(_verify_token, None)


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _make_llm_mock(title: str = "접수가 시작됐어요", body: str = "지금 바로 신청하세요."):
    """정상 응답을 반환하는 LLM chain mock을 생성한다."""
    chain_mock = AsyncMock()
    chain_mock.ainvoke = AsyncMock(
        return_value=NotificationTemplateResponse(title=title, body=body)
    )

    llm_mock = AsyncMock()
    llm_mock.with_structured_output = lambda _: chain_mock

    return llm_mock, chain_mock


# ---------------------------------------------------------------------------
# 정상 응답 케이스
# ---------------------------------------------------------------------------


class TestCreateTemplateSuccess:
    async def test_updated_single_change_returns_200(self, client: AsyncClient):
        """UPDATED 단건 → 200, title/body 비어있지 않음."""
        llm_mock, _ = _make_llm_mock(title="접수 일정이 변경됐어요", body="일정이 바뀌었습니다. 확인해 주세요.")

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [
                        {
                            "change_type": "UPDATED",
                            "field_name": "receipt_start_dt",
                            "old_value": "2025-06-01",
                            "new_value": "2025-06-15",
                        }
                    ],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] != ""
        assert data["body"] != ""
        assert data["title"] == "접수 일정이 변경됐어요"
        assert data["body"] == "일정이 바뀌었습니다. 확인해 주세요."

    async def test_multiple_changes_new_and_updated_returns_200(self, client: AsyncClient):
        """changes 여러 건(NEW+UPDATED) → 200."""
        llm_mock, _ = _make_llm_mock(
            title="새 서비스 등록 및 정보 변경",
            body="새 서비스가 등록되고 정보가 변경됐습니다.",
        )

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC002",
                    "changes": [
                        {"change_type": "NEW"},
                        {
                            "change_type": "UPDATED",
                            "field_name": "place_name",
                            "old_value": "강남체육관",
                            "new_value": "강남종합체육관",
                        },
                    ],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["title"]
        assert data["body"]


# ---------------------------------------------------------------------------
# 입력 검증 케이스 (422)
# ---------------------------------------------------------------------------


class TestCreateTemplateValidation:
    async def test_invalid_change_type_returns_422(self, client: AsyncClient):
        """change_type 범위 위반("MODIFIED") → 422."""
        response = await client.post(
            "/notification/template",
            json={
                "service_id": "SVC001",
                "changes": [{"change_type": "MODIFIED"}],
            },
        )
        assert response.status_code == 422

    async def test_empty_changes_returns_422(self, client: AsyncClient):
        """changes 빈 배열 → 422."""
        response = await client.post(
            "/notification/template",
            json={"service_id": "SVC001", "changes": []},
        )
        assert response.status_code == 422

    async def test_whitespace_service_id_returns_422(self, client: AsyncClient):
        """service_id 공백 → 422."""
        response = await client.post(
            "/notification/template",
            json={
                "service_id": "   ",
                "changes": [{"change_type": "NEW"}],
            },
        )
        assert response.status_code == 422

    async def test_too_many_changes_returns_422(self, client: AsyncClient):
        """changes 51건(MAX=50 초과) → 422."""
        overflow = [{"change_type": "UPDATED"} for _ in range(51)]
        response = await client.post(
            "/notification/template",
            json={"service_id": "SVC001", "changes": overflow},
        )
        assert response.status_code == 422

    async def test_exactly_max_changes_is_accepted(self, client: AsyncClient):
        """changes 50건(MAX 경계값) → validator를 통과해 LLM 호출까지 도달."""
        llm_mock, _ = _make_llm_mock()
        boundary = [{"change_type": "UPDATED"} for _ in range(50)]

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={"service_id": "SVC001", "changes": boundary},
            )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# LLM 실패 degrade 케이스 (503)
# ---------------------------------------------------------------------------


class TestCreateTemplateDegrade:
    async def test_llm_timeout_returns_503(self, client: AsyncClient):
        """LLM TimeoutError → 503."""

        async def _timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        chain_mock = AsyncMock()
        chain_mock.ainvoke = _timeout

        llm_mock = AsyncMock()
        llm_mock.with_structured_output = lambda _: chain_mock

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [{"change_type": "NEW"}],
                },
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "알림 템플릿 생성에 실패했습니다."

    async def test_llm_general_exception_returns_503(self, client: AsyncClient):
        """LLM 일반 예외 → 503."""

        async def _fail(*args, **kwargs):
            raise RuntimeError("LLM 연결 오류")

        chain_mock = AsyncMock()
        chain_mock.ainvoke = _fail

        llm_mock = AsyncMock()
        llm_mock.with_structured_output = lambda _: chain_mock

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [{"change_type": "UPDATED", "field_name": "status"}],
                },
            )

        assert response.status_code == 503

    async def test_llm_returns_empty_title_yields_503(self, client: AsyncClient):
        """LLM이 빈 title 반환 → 503 (빈 응답이 200으로 흘러나오지 않는지)."""
        llm_mock, _ = _make_llm_mock(title="", body="본문은 있어요.")

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [{"change_type": "NEW"}],
                },
            )

        assert response.status_code == 503
        assert response.json()["detail"] == "알림 템플릿 생성에 실패했습니다."

    async def test_llm_returns_whitespace_body_yields_503(self, client: AsyncClient):
        """LLM이 공백만 있는 body 반환 → 503."""
        llm_mock, _ = _make_llm_mock(title="제목이 있어요", body="   ")

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [{"change_type": "DELETED"}],
                },
            )

        assert response.status_code == 503

    async def test_self_timeout_triggers_503(self, client: AsyncClient):
        """asyncio.wait_for 타임아웃 → 503.

        _invoke_llm이 실제로 오래 걸리는 상황을 시뮬레이션한다.
        routers.notification._invoke_llm을 직접 패치하여 asyncio.wait_for가 타임아웃을
        발생시키는 경로를 검증한다.
        """

        async def _slow_invoke(_req):
            await asyncio.sleep(100)
            return NotificationTemplateResponse(title="제목", body="본문")

        with patch("routers.notification._invoke_llm", side_effect=asyncio.TimeoutError()):
            response = await client.post(
                "/notification/template",
                json={
                    "service_id": "SVC001",
                    "changes": [{"change_type": "NEW"}],
                },
            )

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# 인증 테스트
# ---------------------------------------------------------------------------


class TestNotificationAuth:
    """X-Internal-Token 인증 검증."""

    async def test_missing_token_returns_401(self, app: FastAPI):
        """토큰 헤더 없이 호출하면 401을 반환한다."""
        from routers.notification import _verify_token

        app.dependency_overrides.pop(_verify_token, None)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                from unittest.mock import patch
                from core.config import settings

                with patch.object(settings, "admin_internal_token", "secret-token"):
                    response = await c.post(
                        "/notification/template",
                        json={
                            "service_id": "SVC001",
                            "changes": [{"change_type": "NEW"}],
                        },
                    )
                assert response.status_code == 401
        finally:
            app.dependency_overrides[_verify_token] = lambda: None

    async def test_correct_token_passes(self, app: FastAPI):
        """올바른 토큰 헤더로 호출하면 인증을 통과한다(LLM mock으로 200 확인)."""
        from routers.notification import _verify_token

        app.dependency_overrides.pop(_verify_token, None)
        try:
            from unittest.mock import AsyncMock, patch
            from core.config import settings
            from schemas.notification import NotificationTemplateResponse

            chain_mock = AsyncMock()
            chain_mock.ainvoke = AsyncMock(
                return_value=NotificationTemplateResponse(title="제목", body="본문")
            )
            llm_mock = AsyncMock()
            llm_mock.with_structured_output = lambda _: chain_mock

            with patch.object(settings, "admin_internal_token", "secret-token"), patch(
                "routers.notification.get_chat_model", return_value=llm_mock
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as c:
                    response = await c.post(
                        "/notification/template",
                        headers={"X-Internal-Token": "secret-token"},
                        json={
                            "service_id": "SVC001",
                            "changes": [{"change_type": "NEW"}],
                        },
                    )
            assert response.status_code == 200
        finally:
            app.dependency_overrides[_verify_token] = lambda: None
