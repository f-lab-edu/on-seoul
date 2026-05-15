"""POST /chat/stream E2E 테스트 (Phase 16).

FastAPI AsyncClient로 /chat/stream 엔드포인트를 호출하고 SSE 스트림을 검증한다.
LLM 및 DB 호출은 Mock으로 처리한다.

검증 시나리오:
- 첫 질문(title_needed=True)일 때 SSE final 이벤트에 title 포함
- 이후 질문(title_needed=False)일 때 title 없이 응답
- SQL_SEARCH 의도 질의 시 SSE 이벤트 순서 (progress × 3 → final)
- FALLBACK 의도 질의 시 안내 메시지 포함 응답
- 잘못된 요청(room_id/message 없음 등) 422 응답
- 워크플로우 내부 오류 → workflow_error 이벤트 반환
- 세션 레벨 예외 → error 이벤트 반환 (오류 내용 미노출)
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
    """E2E 테스트에서 Redis I/O를 차단한다 (실제 Redis 연결 시도 방지)."""
    with patch("routers.chat.get_recent_queries", new=AsyncMock(return_value=[])), \
         patch("routers.chat.push_recent_query", new=AsyncMock(return_value=None)), \
         patch("routers.chat._resolve_redis", return_value=MagicMock()):
        yield


# ---------------------------------------------------------------------------
# SSE 파싱 헬퍼
# ---------------------------------------------------------------------------


def _parse_sse(content: bytes) -> list[dict]:
    """SSE 응답 바이트를 {event, data} 딕셔너리 리스트로 변환한다."""
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


# ---------------------------------------------------------------------------
# Mock 헬퍼
# ---------------------------------------------------------------------------


_E2E_TRACE = {"node_path": ["router", "sql_agent", "answer"], "elapsed_ms": 50}


def _make_state(**kwargs) -> AgentState:
    return make_agent_state(**{
        "intent": IntentType.SQL_SEARCH,
        "title_needed": True,
        "answer": "강남구 수영장 목록입니다.",
        "trace": _E2E_TRACE,
        **kwargs,
    })


def _session_ctx():
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return _ctx


def _stream_gen(final_state: AgentState):
    """정상 흐름: progress 3개 → result."""
    async def _gen(*args, **kwargs):
        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
        yield "progress", {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."}
        yield "progress", {"step": "answering", "message": "답변을 생성하고 있습니다..."}
        yield "result", final_state

    return _gen


# ---------------------------------------------------------------------------
# 앱 픽스처
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
# 시나리오 1. 첫 질문 — title 포함 여부
# ---------------------------------------------------------------------------


class TestFirstMessageTitle:
    async def test_title_included_in_final_when_message_id_is_1(self, client: AsyncClient, mock_graph):
        """message_id=1 요청 시 final 이벤트에 title 필드가 채워진다."""
        final_state = _make_state(message_id=1, title="수영장 문의", title_needed=True)
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse(response.content)
        final_events = [e for e in events if e.get("event") == "final"]
        assert len(final_events) == 1
        assert final_events[0]["data"]["title"] == "수영장 문의"

    async def test_title_needed_true_passed_to_workflow_for_first_message(
        self, client: AsyncClient, mock_graph
    ):
        """message_id=1이면 AgentState.title_needed=True로 워크플로우에 전달된다."""
        final_state = _make_state(message_id=1, title="첫 제목")
        captured: list[AgentState] = []

        async def _capturing(*args, **kwargs):
            state = args[0]
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is True


# ---------------------------------------------------------------------------
# 시나리오 2. 이후 질문 — title 없이 응답
# ---------------------------------------------------------------------------


class TestSubsequentMessageNoTitle:
    async def test_title_is_none_when_message_id_not_1(self, client: AsyncClient, mock_graph):
        """message_id != 1인 final 이벤트의 title은 None이다."""
        final_state = _make_state(message_id=5, title=None, title_needed=False)
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 5, "message": "몇 시까지야?"},
            )

        events = _parse_sse(response.content)
        final_events = [e for e in events if e.get("event") == "final"]
        assert len(final_events) == 1
        assert final_events[0]["data"]["title"] is None

    async def test_title_needed_false_passed_to_workflow_for_non_first(
        self, client: AsyncClient, mock_graph
    ):
        """message_id != 1이면 AgentState.title_needed=False로 워크플로우에 전달된다."""
        final_state = _make_state(message_id=3, title=None)
        captured: list[AgentState] = []

        async def _capturing(*args, **kwargs):
            state = args[0]
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 3, "message": "더 있어?"},
            )

        assert captured[0]["title_needed"] is False


# ---------------------------------------------------------------------------
# 시나리오 3. SQL_SEARCH 의도 — SSE 이벤트 순서
# ---------------------------------------------------------------------------


class TestSqlSearchEventOrder:
    async def test_sql_search_sse_event_sequence(self, client: AsyncClient, mock_graph):
        """SQL_SEARCH 의도 질의: progress × 3 → final 순서로 SSE 이벤트가 발행된다."""
        final_state = _make_state(intent=IntentType.SQL_SEARCH)
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "지금 접수 중인 수영장"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.content)
        event_types = [e["event"] for e in events]
        assert event_types == ["progress", "progress", "progress", "final"]

    async def test_sql_search_progress_steps_in_order(self, client: AsyncClient, mock_graph):
        """SQL_SEARCH progress 이벤트의 step 값이 routing → searching → answering 순서다."""
        final_state = _make_state(intent=IntentType.SQL_SEARCH)
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "마포구 체육시설"},
            )

        events = _parse_sse(response.content)
        progress_steps = [e["data"]["step"] for e in events if e["event"] == "progress"]
        assert progress_steps == ["routing", "searching", "answering"]

    async def test_final_event_contains_answer_and_intent(self, client: AsyncClient, mock_graph):
        """SQL_SEARCH final 이벤트에 answer와 intent 필드가 채워진다."""
        final_state = _make_state(
            intent=IntentType.SQL_SEARCH,
            answer="강남구 수영장 목록입니다.",
        )
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "수영장"},
            )

        events = _parse_sse(response.content)
        final = next(e for e in events if e["event"] == "final")
        assert final["data"]["answer"] == "강남구 수영장 목록입니다."
        assert final["data"]["intent"] == "SQL_SEARCH"


# ---------------------------------------------------------------------------
# 시나리오 4. FALLBACK 의도 — 안내 메시지 포함
# ---------------------------------------------------------------------------


class TestFallbackScenario:
    async def test_fallback_final_event_contains_answer(self, client: AsyncClient, mock_graph):
        """FALLBACK 의도 질의 시 final 이벤트에 answer가 포함된다."""
        final_state = _make_state(
            intent=IntentType.FALLBACK,
            answer="서울시 공공서비스 예약 챗봇입니다.",
        )
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "안녕하세요"},
            )

        events = _parse_sse(response.content)
        final = next(e for e in events if e["event"] == "final")
        assert "서울시 공공서비스 예약 챗봇입니다." in final["data"]["answer"]
        assert final["data"]["intent"] == "FALLBACK"

    async def test_fallback_event_sequence(self, client: AsyncClient, mock_graph):
        """FALLBACK 의도: progress × 3 → final 이벤트 순서."""
        final_state = _make_state(intent=IntentType.FALLBACK, answer="안내 메시지")
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "반가워요"},
            )

        events = _parse_sse(response.content)
        event_types = [e["event"] for e in events]
        assert event_types == ["progress", "progress", "progress", "final"]


# ---------------------------------------------------------------------------
# 시나리오 5. 잘못된 요청 — 422 응답
# ---------------------------------------------------------------------------


class TestInvalidRequests:
    async def test_missing_room_id_returns_422(self, client: AsyncClient):
        """room_id 누락 → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"message_id": 1, "message": "수영장 알려줘"},
        )
        assert response.status_code == 422

    async def test_missing_message_id_returns_422(self, client: AsyncClient):
        """message_id 누락 → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"room_id": 1, "message": "수영장 알려줘"},
        )
        assert response.status_code == 422

    async def test_missing_message_returns_422(self, client: AsyncClient):
        """message 누락 → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"room_id": 1, "message_id": 1},
        )
        assert response.status_code == 422

    async def test_invalid_lat_out_of_range_returns_422(self, client: AsyncClient):
        """lat=-91.0 (범위 초과) → 422 반환."""
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

    async def test_invalid_lng_out_of_range_returns_422(self, client: AsyncClient):
        """lng=181.0 (범위 초과) → 422 반환."""
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

    async def test_empty_message_returns_422(self, client: AsyncClient):
        """message가 빈 문자열 → 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={"room_id": 1, "message_id": 1, "message": ""},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# 시나리오 6. 워크플로우 내부 오류 — workflow_error 이벤트
# ---------------------------------------------------------------------------


class TestWorkflowInternalError:
    async def test_workflow_internal_error_yields_workflow_error_event(
        self, client: AsyncClient, mock_graph
    ):
        """워크플로우가 error 필드를 담아 result를 반환하면 workflow_error 이벤트가 발행된다."""
        final_state = _make_state(
            message_id=2,
            error="LLM 오류",
            answer="죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )

        async def _error_stream(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.content)
        event_types = [e["event"] for e in events]
        assert "workflow_error" in event_types
        assert "final" not in event_types

        workflow_error = next(e for e in events if e["event"] == "workflow_error")
        assert workflow_error["data"]["error"] == "서비스 처리 중 오류가 발생했습니다."
        assert workflow_error["data"]["message_id"] == 2

    async def test_workflow_error_event_follows_progress(self, client: AsyncClient, mock_graph):
        """workflow_error 이벤트는 progress 이벤트 이후에 발행된다."""
        final_state = _make_state(error="검색 실패", answer="죄송합니다.")

        async def _error_stream(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse(response.content)
        event_types = [e["event"] for e in events]
        assert event_types[0] == "progress"
        assert event_types[-1] == "workflow_error"


# ---------------------------------------------------------------------------
# 시나리오 7. 세션 레벨 예외 — error 이벤트 (오류 내용 미노출)
# ---------------------------------------------------------------------------


class TestSessionLevelError:
    async def test_session_exception_returns_generic_error_event(self, client: AsyncClient, mock_graph):
        """세션/DB 레벨 예외 → error 이벤트가 1개 반환되고 오류 내용이 노출되지 않는다."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("DB 연결 실패 내부 정보"))

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert response.status_code == 200
        events = _parse_sse(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"
        # 오류 내부 정보는 노출되지 않는다
        assert "DB 연결 실패 내부 정보" not in events[0]["data"]["message"]
        assert events[0]["data"]["message"] == "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

    async def test_session_exception_only_one_error_event(self, client: AsyncClient, mock_graph):
        """세션 레벨 예외 시 SSE 이벤트가 정확히 1개만 발행된다."""
        mock_graph.stream = MagicMock(side_effect=ValueError("세션 오류"))

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        events = _parse_sse(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"


# ---------------------------------------------------------------------------
# 시나리오 8. 응답 헤더 및 Content-Type
# ---------------------------------------------------------------------------


class TestSseResponseHeaders:
    async def test_content_type_is_event_stream(self, client: AsyncClient, mock_graph):
        """SSE 응답의 Content-Type은 text/event-stream이다."""
        final_state = _make_state()
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert "text/event-stream" in response.headers["content-type"]

    async def test_sse_anti_buffering_headers_present(self, client: AsyncClient, mock_graph):
        """Cache-Control, Connection, X-Accel-Buffering 헤더가 설정된다."""
        final_state = _make_state()
        mock_graph.stream = _stream_gen(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _session_ctx()),
            patch("routers.chat.data_session_ctx", _session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("connection") == "keep-alive"
        assert response.headers.get("x-accel-buffering") == "no"
