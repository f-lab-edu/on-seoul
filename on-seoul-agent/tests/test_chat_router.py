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
    with (
        patch("routers.chat.get_recent_queries", new=AsyncMock(return_value=[])),
        patch("routers.chat.push_recent_query", new=AsyncMock(return_value=None)),
        patch("routers.chat._resolve_redis", return_value=MagicMock()),
    ):
        yield


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


_CHAT_TRACE = {"node_path": ["router", "sql_agent", "answer"], "elapsed_ms": 100}


def _make_final_state(**kwargs) -> AgentState:
    return make_agent_state(
        **{
            "intent": IntentType.SQL_SEARCH,
            "answer": "강남구 수영장 목록입니다.",
            "trace": _CHAT_TRACE,
            **kwargs,
        }
    )


def _parse_sse_events(content: bytes) -> list[dict]:
    """SSE 응답 바이트를 파싱하여 {event, data} 목록으로 반환한다."""
    events: list[dict] = []
    current: dict = {}
    for line in content.decode().splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: ") :]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: ") :])
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
        yield (
            "progress",
            {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."},
        )
        yield (
            "progress",
            {"step": "answering", "message": "답변을 생성하고 있습니다..."},
        )
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------


class TestChatStreamRouter:
    async def test_normal_request_returns_final_event(
        self, client: AsyncClient, mock_graph
    ):
        """정상 요청 → status 200, final 이벤트 포함."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

    async def test_first_message_sets_title_needed(
        self, client: AsyncClient, mock_graph
    ):
        """message_id=1이면 title_needed=True로 워크플로우가 호출된다."""
        final_state = _make_final_state(
            message_id=1, title="수영장 조회", title_needed=True
        )
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is True

    async def test_non_first_message_sets_title_needed_false(
        self, client: AsyncClient, mock_graph
    ):
        """message_id != 1이면 title_needed=False로 워크플로우가 호출된다."""
        final_state = _make_final_state(message_id=5, title=None, title_needed=False)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 5, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is False

    async def test_workflow_exception_returns_error_event(
        self, client: AsyncClient, mock_graph
    ):
        """세션/DB 레벨 예외 → error 이벤트 반환."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("LLM 타임아웃"))

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

    async def test_boundary_lat_exactly_90_is_valid(
        self, client: AsyncClient, mock_graph
    ):
        """lat=90.0 경계값은 유효하므로 422가 아니어야 한다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

    async def test_sse_stream_yields_progress_then_final(
        self, client: AsyncClient, mock_graph
    ):
        """정상 요청 시 progress 이벤트 3개 후 final 이벤트가 발행된다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        event_types = [e["event"] for e in events]
        # 3개의 progress + 1개의 final
        assert event_types == ["progress", "progress", "progress", "final"]

    async def test_error_stream_yields_exactly_one_event(
        self, client: AsyncClient, mock_graph
    ):
        """세션/DB 레벨 예외 시 SSE 이벤트가 정확히 1개(error)만 발행된다."""
        mock_graph.stream = MagicMock(side_effect=ValueError("DB 연결 실패"))

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"

    async def test_error_event_message_is_generic(
        self, client: AsyncClient, mock_graph
    ):
        """error 이벤트의 message 필드는 예외 내용을 노출하지 않고 범용 문자열을 반환한다."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("LLM 타임아웃 발생"))

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert (
            events[0]["data"]["message"]
            == "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        )
        assert "LLM 타임아웃 발생" not in events[0]["data"]["message"]

    async def test_final_event_includes_title_when_title_needed(
        self, client: AsyncClient, mock_graph
    ):
        """message_id=1 요청의 final 이벤트에 title 필드가 채워진다."""
        final_state = _make_final_state(
            message_id=1, title="수영장 문의", title_needed=True
        )
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["title"] == "수영장 문의"

    async def test_final_event_title_is_none_for_non_first_message(
        self, client: AsyncClient, mock_graph
    ):
        """message_id != 1 요청의 final 이벤트에서 title은 None이다."""
        final_state = _make_final_state(message_id=3, title=None, title_needed=False)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

    async def test_workflow_internal_error_returns_workflow_error_event(
        self, client: AsyncClient, mock_graph
    ):
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
            yield (
                "progress",
                {"step": "routing", "message": "질문을 분석하고 있습니다..."},
            )
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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
        assert (
            data["answer"]
            == "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        )
        assert data["message_id"] == 2

    async def test_workflow_error_event_order_is_progress_then_workflow_error(
        self, client: AsyncClient, mock_graph
    ):
        """workflow_error 이벤트는 progress 이벤트 이후에 발행된다."""
        final_state = _make_final_state(
            error="검색 실패",
            answer="죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
        )

        async def _error_stream(*args, **kwargs):
            yield (
                "progress",
                {"step": "routing", "message": "질문을 분석하고 있습니다..."},
            )
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
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

    async def test_cache_hit_sse_payload_marks_cache_hit(
        self, client: AsyncClient, mock_graph
    ):
        """result.cache_hit=True이면 final SSE payload에 cache_hit=True 포함."""
        final_state = _make_final_state(cache_hit=True)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["cache_hit"] is True

    async def test_cache_miss_sse_payload_marks_cache_hit_false(
        self, client: AsyncClient, mock_graph
    ):
        """기본값(cache_hit=False)이면 final SSE payload에 cache_hit=False."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["cache_hit"] is False

    async def test_recent_queries_passed_into_state(
        self, client: AsyncClient, mock_graph
    ):
        """fetch한 recent_queries가 AgentState에 그대로 주입된다."""
        final_state = _make_final_state()
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
            patch(
                "routers.chat.get_recent_queries",
                new=AsyncMock(return_value=["이전 질문1", "이전 질문2"]),
            ),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 7, "message_id": 3, "message": "성동구는?"},
            )

        assert captured[0]["recent_queries"] == ["이전 질문1", "이전 질문2"]

    async def test_recent_queries_pushed_after_success(
        self, client: AsyncClient, mock_graph
    ):
        """정상 final 응답 후 사용자 message가 recent_queries 큐에 push된다."""
        final_state = _make_final_state()
        push_mock = AsyncMock(return_value=None)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
            patch("routers.chat.push_recent_query", new=push_mock),
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

    async def test_recent_queries_not_pushed_on_workflow_error(
        self, client: AsyncClient, mock_graph
    ):
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

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
            patch("routers.chat.push_recent_query", new=push_mock),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        push_mock.assert_not_awaited()

    async def test_recent_queries_not_pushed_on_session_error(
        self, client: AsyncClient, mock_graph
    ):
        """세션/DB 레벨 예외(error 이벤트) 시 push_recent_query 미수행."""
        push_mock = AsyncMock(return_value=None)
        mock_graph.stream = MagicMock(side_effect=RuntimeError("DB 다운"))

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
            patch("routers.chat.push_recent_query", new=push_mock),
        ):
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        push_mock.assert_not_awaited()


class TestServiceCardsInFinalPayload:
    """SSE final 이벤트의 service_cards 구조화 배열 검증."""

    async def test_final_payload_includes_service_cards(
        self, client: AsyncClient, mock_graph
    ):
        """AnswerAgent 가 채운 service_cards 가 SSE final payload 에 그대로 노출된다."""
        cards = [
            {"service_id": "S1", "service_name": "수영장", "area_name": "강남구"},
            {"service_id": "S2", "service_name": "체육관", "area_name": "마포구"},
        ]
        final_state = _make_final_state(service_cards=cards)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["service_cards"] == cards

    async def test_final_payload_service_cards_empty_when_unset(
        self, client: AsyncClient, mock_graph
    ):
        """service_cards 가 None (예: 구버전 cache hit) 이어도 [] 로 안전 노출된다."""
        final_state = _make_final_state(service_cards=None)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["service_cards"] == []

    async def test_final_payload_preserves_existing_keys_alongside_service_cards(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: service_cards 추가 후에도 기존 final payload 키가 모두 유지된다.

        message_id / answer / intent / title / cache_hit 5개 키가 모두 그대로
        존재해야 한다. service_cards 도입으로 인한 누락 회귀를 방지한다.
        """
        cards = [{"service_id": "S1", "service_name": "수영장"}]
        final_state = _make_final_state(
            service_cards=cards,
            title="수영장 안내",
            cache_hit=False,
        )
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        data = final_events[0]["data"]
        expected_keys = {
            "message_id",
            "answer",
            "intent",
            "title",
            "cache_hit",
            "service_cards",
        }
        assert expected_keys.issubset(set(data.keys()))
        assert data["title"] == "수영장 안내"
        assert data["cache_hit"] is False
        assert data["intent"] == IntentType.SQL_SEARCH.value
        assert data["answer"] == "강남구 수영장 목록입니다."

    async def test_cache_hit_final_payload_carries_restored_service_cards(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: cache_hit=True 경로에서도 service_cards 가 final payload 에 실린다.

        실제 CacheCheckNode 가 envelope payload 에서 service_cards 를 복원해
        state 에 채운 상황을 모사한다. AnswerAgent 미실행 경로에서도 동일하게
        프론트 카드 UI 가 데이터를 받을 수 있어야 한다.
        """
        cards = [
            {"service_id": "S1", "service_name": "캐시된 수영장"},
            {"service_id": "S2", "service_name": "캐시된 체육관"},
        ]
        final_state = _make_final_state(cache_hit=True, service_cards=cards)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        data = final_events[0]["data"]
        assert data["cache_hit"] is True
        assert data["service_cards"] == cards

    async def test_sse_frame_serializes_datetime_in_service_cards(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: service_cards 에 datetime 객체가 포함되어도 SSE 직렬화가 깨지지 않는다.

        public_service_reservations 의 receipt_*_dt 컬럼은 timestamp 타입이라
        SQLAlchemy 가 datetime 객체로 매핑한다. sse_frame() 의 json.dumps 가
        default=str 폴백을 적용해 ISO 8601 문자열로 직렬화해야 하며, 그렇지
        않으면 TypeError 로 SSE 스트림이 중단된다.
        """
        import datetime as _dt

        cards = [
            {
                "service_id": "S1",
                "service_name": "수영장",
                "receipt_start_dt": _dt.datetime(2025, 11, 1, 9, 0, 0),
                "receipt_end_dt": _dt.datetime(2025, 12, 31, 18, 0, 0),
            }
        ]
        final_state = _make_final_state(service_cards=cards)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        # TypeError 가 발생하지 않고 정상 SSE 응답이 흘러야 한다.
        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert len(final_events) == 1
        data = final_events[0]["data"]
        # default=str 폴백에 의해 datetime 이 문자열로 직렬화된다.
        card = data["service_cards"][0]
        assert isinstance(card["receipt_start_dt"], str)
        assert isinstance(card["receipt_end_dt"], str)
        assert card["receipt_start_dt"].startswith("2025-11-01")
        assert card["receipt_end_dt"].startswith("2025-12-31")

    async def test_sse_frame_serializes_decimal_and_date_in_service_cards(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: service_cards 에 Decimal / date 가 포함돼도 SSE 직렬화가 깨지지 않는다.

        DB numeric 컬럼은 SQLAlchemy 가 Decimal 로, date 컬럼은 datetime.date 로
        매핑한다. 둘 다 json 기본 직렬화 대상이 아니므로 default=str 폴백이 없으면
        TypeError 로 SSE 스트림이 중단된다. datetime 만 커버하던 기존 회귀 테스트의
        사각지대를 메운다.
        """
        import datetime as _dt
        from decimal import Decimal

        cards = [
            {
                "service_id": "S1",
                "service_name": "수영장",
                "fee": Decimal("3000.50"),
                "open_date": _dt.date(2025, 12, 31),
            }
        ]
        final_state = _make_final_state(service_cards=cards)
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert len(final_events) == 1
        card = final_events[0]["data"]["service_cards"][0]
        # default=str 폴백으로 Decimal / date 가 문자열로 직렬화된다.
        assert card["fee"] == "3000.50"
        assert card["open_date"] == "2025-12-31"

    async def test_existing_sse_events_unaffected_by_default_str(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: default=str 추가가 기존 SSE 이벤트(progress/final) 직렬화를 바꾸지 않는다.

        progress payload 등 JSON-native 값만 담은 기존 이벤트는 default=str 폴백이
        적용될 일이 없어야 하며, 값이 그대로 직렬화돼야 한다 (문자열 강제 변환 등
        부작용 없음).
        """
        final_state = _make_final_state(service_cards=[])
        mock_graph.stream = _make_stream(final_state)

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        progress_events = [e for e in events if e["event"] == "progress"]
        assert len(progress_events) == 3
        # step 문자열, message 문자열이 그대로 유지된다.
        assert progress_events[0]["data"]["step"] == "routing"
        # final 의 cache_hit 은 bool 그대로 (str 로 강제되지 않음).
        final_data = [e for e in events if e["event"] == "final"][0]["data"]
        assert final_data["cache_hit"] is False
        assert isinstance(final_data["cache_hit"], bool)

    async def test_workflow_error_payload_handles_service_cards_safely(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: workflow_error 경로에서는 service_cards 가 항상 빈 배열로 강제된다.

        에러 메시지 + 부분 결과 카드 동시 노출은 사용자 UI 혼란을 유발하므로,
        라우터가 workflow_error 분기에서 명시적으로 [] 로 덮어쓰는 정책이다.
        state.service_cards 가 None 이든 부분 결과를 담고 있든 동일하게 [] 가 노출된다.
        """
        # 부분 결과가 state 에 남아 있어도 에러 분기에서는 노출되지 않아야 한다.
        partial_cards = [{"service_id": "S1", "service_name": "부분 결과"}]
        final_state = _make_final_state(
            error="LLM 오류",
            answer="죄송합니다, 일시적인 오류가 발생했습니다.",
            service_cards=partial_cards,
        )

        async def _error_stream(*args, **kwargs):
            yield (
                "progress",
                {"step": "routing", "message": "질문을 분석하고 있습니다..."},
            )
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        wf_error_events = [e for e in events if e["event"] == "workflow_error"]
        assert len(wf_error_events) == 1
        data = wf_error_events[0]["data"]
        assert data["service_cards"] == []
        assert data["error"] == "서비스 처리 중 오류가 발생했습니다."

    async def test_workflow_error_forces_empty_cards_when_answer_absent(
        self, client: AsyncClient, mock_graph
    ):
        """회귀: 'answer 없는 error' 경로에서도 service_cards 가 [] 로 강제된다.

        기존 회귀 테스트는 answer 가 채워진 error 만 검증했다. answer=None 이고
        service_cards 가 None 인(AnswerAgent 미실행) error 양쪽 분기에서도 동일하게
        [] 가 노출되어야 한다.
        """
        final_state = _make_final_state(
            error="라우팅 실패",
            answer=None,
            service_cards=None,
        )

        async def _error_stream(*args, **kwargs):
            yield (
                "progress",
                {"step": "routing", "message": "질문을 분석하고 있습니다..."},
            )
            yield "result", final_state

        mock_graph.stream = _error_stream

        with (
            patch("routers.chat.ai_session_ctx", _make_session_ctx()),
            patch("routers.chat.data_session_ctx", _make_session_ctx()),
        ):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        wf_error_events = [e for e in events if e["event"] == "workflow_error"]
        assert len(wf_error_events) == 1
        assert wf_error_events[0]["data"]["service_cards"] == []


class TestMainEndpoints:
    """main.py 전역 핸들러 및 헬스체크 테스트."""

    @pytest.fixture()
    def app(self) -> FastAPI:
        from main import app as _app

        return _app

    @pytest.fixture()
    async def client(self, app: FastAPI) -> AsyncClient:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
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
