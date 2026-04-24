"""POST /chat/stream 라우터 테스트.

httpx.AsyncClient로 SSE 스트리밍을 검증한다.
AgentWorkflow는 AsyncMock으로 패치하여 LLM/DB 호출 없이 단위 테스트한다.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from schemas.state import AgentState, IntentType

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_final_state(**kwargs) -> AgentState:
    base = AgentState(
        room_id=1,
        message_id=1,
        message="수영장 알려줘",
        title_needed=False,
        intent=IntentType.SQL_SEARCH,
        lat=None,
        lng=None,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer="강남구 수영장 목록입니다.",
        title=None,
        trace={"node_path": ["router", "sql_agent", "answer"], "elapsed_ms": 100},
        error=None,
    )
    base.update(kwargs)
    return base


def _parse_sse_events(content: bytes) -> list[dict]:
    """SSE 응답 바이트를 파싱하여 {event, data} 목록으로 반환한다."""
    events: list[dict] = []
    current: dict = {}
    for line in content.decode().splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: "):]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: "):])
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def _make_session_ctx():
    """asynccontextmanager로 MagicMock 세션을 yield하는 픽스처 헬퍼."""
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx


# ---------------------------------------------------------------------------
# 앱 픽스처 — main.py app을 import해 재사용
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    from main import app as _app

    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------


class TestChatStreamRouter:
    async def test_normal_request_returns_final_event(self, client: AsyncClient):
        """정상 요청 → status 200, final 이벤트 포함."""
        final_state = _make_final_state()

        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e.get("event") == "final"]
        assert len(final_events) == 1

        data = final_events[0]["data"]
        assert data["message_id"] == 1
        assert data["answer"] == "강남구 수영장 목록입니다."
        assert data["intent"] == "SQL_SEARCH"

    async def test_first_message_sets_title_needed(self, client: AsyncClient):
        """message_id=1이면 title_needed=True로 워크플로우가 호출된다."""
        final_state = _make_final_state(message_id=1, title="수영장 조회", title_needed=True)

        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        call_kwargs = mock_run.call_args
        passed_state: AgentState = call_kwargs[0][0]
        assert passed_state["title_needed"] is True

    async def test_non_first_message_sets_title_needed_false(self, client: AsyncClient):
        """message_id != 1이면 title_needed=False로 워크플로우가 호출된다."""
        final_state = _make_final_state(message_id=5, title=None, title_needed=False)

        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 5, "message": "수영장 알려줘"},
            )

        call_kwargs = mock_run.call_args
        passed_state: AgentState = call_kwargs[0][0]
        assert passed_state["title_needed"] is False

    async def test_workflow_exception_returns_error_event(self, client: AsyncClient):
        """워크플로우 예외 → error 이벤트 반환."""
        mock_run = AsyncMock(side_effect=RuntimeError("LLM 타임아웃"))
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert "message" in error_events[0]["data"]

    async def test_invalid_lat_returns_422(self, client: AsyncClient):
        """잘못된 lat 범위(-91.0) → 422 반환 (Pydantic 검증)."""
        response = await client.post(
            "/chat/stream",
            json={
                "room_id": 1,
                "message_id": 1,
                "message": "내 주변 체육관",
                "lat": -91.0,
                "lng": 126.9780,
            },
        )
        assert response.status_code == 422

    async def test_response_headers_for_sse(self, client: AsyncClient):
        """SSE 응답에 Cache-Control, Connection, X-Accel-Buffering 헤더가 포함된다."""
        final_state = _make_final_state()
        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("connection") == "keep-alive"
        assert response.headers.get("x-accel-buffering") == "no"

    # ------------------------------------------------------------------
    # 추가 엣지케이스
    # ------------------------------------------------------------------

    async def test_invalid_lng_returns_422(self, client: AsyncClient):
        """잘못된 lng 범위(181.0) → 422 반환 (Pydantic 검증)."""
        response = await client.post(
            "/chat/stream",
            json={
                "room_id": 1,
                "message_id": 1,
                "message": "내 주변 체육관",
                "lat": 37.5,
                "lng": 181.0,
            },
        )
        assert response.status_code == 422

    async def test_boundary_lat_exactly_90_is_valid(self, client: AsyncClient):
        """lat=90.0 경계값은 유효하므로 422가 아니어야 한다."""
        final_state = _make_final_state()
        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={
                    "room_id": 1,
                    "message_id": 1,
                    "message": "테스트",
                    "lat": 90.0,
                    "lng": 180.0,
                },
            )

        assert response.status_code == 200

    async def test_sse_stream_yields_exactly_one_event(self, client: AsyncClient):
        """정상 요청 시 SSE 이벤트가 정확히 1개 발행되고 final 또는 error 중 하나임을 보장한다."""
        final_state = _make_final_state()
        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert len(events) == 1
        assert events[0]["event"] in ("final", "error")

    async def test_error_stream_yields_exactly_one_event(self, client: AsyncClient):
        """워크플로우 예외 시에도 SSE 이벤트가 정확히 1개(error)만 발행된다."""
        mock_run = AsyncMock(side_effect=ValueError("DB 연결 실패"))
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"

    async def test_error_event_message_is_generic(self, client: AsyncClient):
        """error 이벤트의 message 필드는 예외 내용을 노출하지 않고 범용 문자열을 반환한다."""
        mock_run = AsyncMock(side_effect=RuntimeError("LLM 타임아웃 발생"))
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert events[0]["data"]["message"] == "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        assert "LLM 타임아웃 발생" not in events[0]["data"]["message"]

    async def test_final_event_includes_title_when_title_needed(self, client: AsyncClient):
        """message_id=1 요청의 final 이벤트에 title 필드가 채워진다."""
        final_state = _make_final_state(message_id=1, title="수영장 문의", title_needed=True)
        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        assert events[0]["data"]["title"] == "수영장 문의"

    async def test_final_event_title_is_none_for_non_first_message(self, client: AsyncClient):
        """message_id != 1 요청의 final 이벤트에서 title은 None이다."""
        final_state = _make_final_state(message_id=3, title=None, title_needed=False)
        mock_run = AsyncMock(return_value=final_state)
        with patch("routers.chat._workflow") as mock_wf, patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            mock_wf.run = mock_run

            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 3, "message": "수영장 몇 시까지야"},
            )

        events = _parse_sse_events(response.content)
        assert events[0]["data"]["title"] is None

    async def test_missing_required_field_returns_422(self, client: AsyncClient):
        """필수 필드 누락(message 없음) → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"room_id": 1, "message_id": 1},
        )
        assert response.status_code == 422

    async def test_missing_room_id_returns_422(self, client: AsyncClient):
        """필수 필드 누락(room_id 없음) → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"message_id": 1, "message": "테스트"},
        )
        assert response.status_code == 422


class TestMainEndpoints:
    """main.py 전역 핸들러 및 헬스체크 테스트."""

    @pytest.fixture()
    def app(self) -> FastAPI:
        from main import app as _app

        return _app

    @pytest.fixture()
    async def client(self, app: FastAPI) -> AsyncClient:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    async def test_health_returns_ok(self, client: AsyncClient):
        """GET /health → 200, {"status": "ok"}."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_global_500_handler_returns_json(self):
        """라우터에서 발생한 RuntimeError → 전역 핸들러가 500 JSON을 반환해야 한다.

        BaseHTTPMiddleware 기반 catch-all이 route handler 내부의 RuntimeError를 잡아
        500 JSON으로 변환하는지 검증하는 회귀 테스트이다.
        프로덕션 앱을 오염시키지 않도록 별도 FastAPI 인스턴스를 사용한다.
        """
        from fastapi import APIRouter, FastAPI

        from main import _CatchAllMiddleware

        isolated_app = FastAPI()
        isolated_app.add_middleware(_CatchAllMiddleware)

        test_router = APIRouter()

        @test_router.get("/test-500-regression")
        async def _raise():
            raise RuntimeError("의도적 500")

        isolated_app.include_router(test_router)

        async with AsyncClient(
            transport=ASGITransport(app=isolated_app), base_url="http://test"
        ) as c:
            response = await c.get("/test-500-regression")

        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}
