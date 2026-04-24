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
