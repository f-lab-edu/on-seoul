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

from tests.helpers import make_agent_state
from schemas.state import AgentState, IntentType


@pytest.fixture(autouse=True)
def _mock_redis_io():
    """모든 chat router 테스트에서 Redis I/O를 차단한다.

    routers.chat 모듈이 호출하는 get_recent_queries / push_recent_query /
    _resolve_redis를 mock으로 대체하여 실제 Redis 연결 시도 없이 동작하도록 한다.
    """
    with patch("routers.chat.get_recent_queries", new=AsyncMock(return_value=[])), \
         patch("routers.chat.push_recent_query", new=AsyncMock(return_value=None)), \
         patch("routers.chat._resolve_redis", return_value=MagicMock()):
        yield

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


_CHAT_TRACE = {"node_path": ["router", "sql_agent", "answer"], "elapsed_ms": 100}


def _make_final_state(**kwargs) -> AgentState:
    return make_agent_state(**{
        "intent": IntentType.SQL_SEARCH,
        "answer": "강남구 수영장 목록입니다.",
        "trace": _CHAT_TRACE,
        **kwargs,
    })


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


def _make_stream(final_state: AgentState):
    """workflow.stream()을 모사하는 async generator factory를 반환한다.

    정상 흐름: progress 3개(routing → searching → answering) + result 1개.
    """
    async def _gen(*args, **kwargs):
        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
        yield "progress", {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."}
        yield "progress", {"step": "answering", "message": "답변을 생성하고 있습니다..."}
        yield "result", final_state

    return _gen


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
    async def test_normal_request_returns_final_event(self, client: AsyncClient, mock_graph):
        """정상 요청 → status 200, final 이벤트 포함."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
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

    async def test_first_message_sets_title_needed(self, client: AsyncClient, mock_graph):
        """message_id=1이면 title_needed=True로 워크플로우가 호출된다."""
        final_state = _make_final_state(message_id=1, title="수영장 조회", title_needed=True)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is True

    async def test_non_first_message_sets_title_needed_false(self, client: AsyncClient, mock_graph):
        """message_id != 1이면 title_needed=False로 워크플로우가 호출된다."""
        final_state = _make_final_state(message_id=5, title=None, title_needed=False)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 5, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is False

    async def test_workflow_exception_returns_error_event(self, client: AsyncClient, mock_graph):
        """세션/DB 레벨 예외 → error 이벤트 반환."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("LLM 타임아웃"))

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
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

    async def test_response_headers_for_sse(self, client: AsyncClient, mock_graph):
        """SSE 응답에 Cache-Control, Connection, X-Accel-Buffering 헤더가 포함된다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
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

    async def test_boundary_lat_exactly_90_is_valid(self, client: AsyncClient, mock_graph):
        """lat=90.0 경계값은 유효하므로 422가 아니어야 한다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
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

    async def test_sse_stream_yields_progress_then_final(self, client: AsyncClient, mock_graph):
        """정상 요청 시 progress 이벤트 3개 후 final 이벤트가 발행된다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        event_types = [e["event"] for e in events]
        # 3개의 progress + 1개의 final
        assert event_types == ["progress", "progress", "progress", "final"]

    async def test_error_stream_yields_exactly_one_event(self, client: AsyncClient, mock_graph):
        """세션/DB 레벨 예외 시 SSE 이벤트가 정확히 1개(error)만 발행된다."""
        mock_graph.stream = MagicMock(side_effect=ValueError("DB 연결 실패"))

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"

    async def test_error_event_message_is_generic(self, client: AsyncClient, mock_graph):
        """error 이벤트의 message 필드는 예외 내용을 노출하지 않고 범용 문자열을 반환한다."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("LLM 타임아웃 발생"))

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert events[0]["data"]["message"] == "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        assert "LLM 타임아웃 발생" not in events[0]["data"]["message"]

    async def test_final_event_includes_title_when_title_needed(self, client: AsyncClient, mock_graph):
        """message_id=1 요청의 final 이벤트에 title 필드가 채워진다."""
        final_state = _make_final_state(message_id=1, title="수영장 문의", title_needed=True)
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["title"] == "수영장 문의"

    async def test_final_event_title_is_none_for_non_first_message(self, client: AsyncClient, mock_graph):
        """message_id != 1 요청의 final 이벤트에서 title은 None이다."""
        final_state = _make_final_state(message_id=3, title=None, title_needed=False)
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 3, "message": "수영장 몇 시까지야"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["title"] is None

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

    async def test_workflow_internal_error_returns_workflow_error_event(self, client: AsyncClient, mock_graph):
        """워크플로우 내부 오류(error 필드 있음) → workflow_error 이벤트가 발행된다.

        workflow.stream()이 result에 error를 담아 반환하는 경우(fallback 답변 포함)
        _stream()이 workflow_error 프레임을 발행해야 한다.
        """
        final_state = _make_final_state(
            message_id=2,
            error="LLM 오류",
            answer="죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )

        async def _error_stream(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
            yield "result", final_state

        mock_graph.stream = _error_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        event_types = [e["event"] for e in events]
        assert "workflow_error" in event_types

        workflow_error_events = [e for e in events if e["event"] == "workflow_error"]
        assert len(workflow_error_events) == 1
        data = workflow_error_events[0]["data"]
        assert data["error"] == "서비스 처리 중 오류가 발생했습니다."
        assert data["answer"] == "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        assert data["message_id"] == 2

    async def test_workflow_error_event_order_is_progress_then_workflow_error(self, client: AsyncClient, mock_graph):
        """workflow_error 이벤트는 progress 이벤트 이후에 발행된다."""
        final_state = _make_final_state(
            error="검색 실패",
            answer="죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )

        async def _error_stream(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
            yield "result", final_state

        mock_graph.stream = _error_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        event_types = [e["event"] for e in events]
        assert event_types[0] == "progress"
        assert event_types[-1] == "workflow_error"
        # final 이벤트는 발행되지 않는다
        assert "final" not in event_types


class TestCacheAndContextIntegration:
    """Answer Cache & Conversation Context 통합 동작 검증."""

    async def test_cache_hit_sse_payload_marks_cache_hit(self, client: AsyncClient, mock_graph):
        """result.cache_hit=True이면 final SSE payload에 cache_hit=True 포함."""
        final_state = _make_final_state(cache_hit=True)
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["cache_hit"] is True

    async def test_cache_miss_sse_payload_marks_cache_hit_false(self, client: AsyncClient, mock_graph):
        """기본값(cache_hit=False)이면 final SSE payload에 cache_hit=False."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["cache_hit"] is False

    async def test_recent_queries_passed_into_state(self, client: AsyncClient, mock_graph):
        """fetch한 recent_queries가 AgentState에 그대로 주입된다."""
        final_state = _make_final_state()
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()), patch(
            "routers.chat.get_recent_queries", new=AsyncMock(return_value=["이전 질문1", "이전 질문2"])
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 7, "message_id": 3, "message": "성동구는?"},
            )

        assert captured[0]["recent_queries"] == ["이전 질문1", "이전 질문2"]

    async def test_recent_queries_pushed_after_success(self, client: AsyncClient, mock_graph):
        """정상 final 응답 후 사용자 message가 recent_queries 큐에 push된다."""
        final_state = _make_final_state()
        push_mock = AsyncMock(return_value=None)
        mock_graph.stream = _make_stream(final_state)

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()), patch(
            "routers.chat.push_recent_query", new=push_mock
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 42, "message_id": 2, "message": "수영장 알려줘"},
            )

        assert push_mock.await_count == 1
        args, _ = push_mock.call_args
        # push_recent_query(room_id, message, redis)
        assert args[0] == 42
        assert args[1] == "수영장 알려줘"

    async def test_recent_queries_not_pushed_on_workflow_error(self, client: AsyncClient, mock_graph):
        """workflow_error 응답에서는 push_recent_query 미수행."""
        final_state = _make_final_state(
            error="LLM 오류",
            answer="죄송합니다, 일시적인 오류가 발생했습니다.",
        )

        async def _error_stream(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        push_mock = AsyncMock(return_value=None)
        mock_graph.stream = _error_stream

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()), patch(
            "routers.chat.push_recent_query", new=push_mock
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        push_mock.assert_not_awaited()

    async def test_recent_queries_not_pushed_on_session_error(self, client: AsyncClient, mock_graph):
        """세션/DB 레벨 예외(error 이벤트) 시 push_recent_query 미수행."""
        push_mock = AsyncMock(return_value=None)
        mock_graph.stream = MagicMock(side_effect=RuntimeError("DB 다운"))

        with patch(
            "routers.chat.ai_session_ctx", _make_session_ctx()
        ), patch("routers.chat.data_session_ctx", _make_session_ctx()), patch(
            "routers.chat.push_recent_query", new=push_mock
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        push_mock.assert_not_awaited()


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
