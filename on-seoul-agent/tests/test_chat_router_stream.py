"""POST /chat/stream 라우터 SSE 스트리밍 테스트.

httpx.AsyncClient로 SSE 스트리밍을 검증한다.
AgentWorkflow는 AsyncMock으로 패치하여 LLM/DB 호출 없이 단위 테스트한다.
"""

from contextlib import nullcontext
from unittest.mock import MagicMock

from httpx import AsyncClient

from core.exceptions import RateLimitException
from schemas.state import AgentState, IntentType
from tests._chat_router_support import (
    _CHAT_TRACE,  # noqa: F401
    _make_final_state,
    _make_stream,
    _mock_redis_io,  # noqa: F401
    _parse_sse_events,
    app,  # noqa: F401
    client,  # noqa: F401
)


class TestChatStreamRouter:
    async def test_normal_request_returns_final_event(
        self, client: AsyncClient, mock_graph
    ):
        """정상 요청 → status 200, final 이벤트 포함."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
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

    async def test_title_needed_true_propagated_from_request(
        self, client: AsyncClient, mock_graph
    ):
        """요청의 title_needed=true → AgentState.title_needed=True. message_id 무관."""
        # message_id 는 전역 PK 라 첫 턴이어도 1 이 아니다(234). 플래그만으로 게이트됨을 단언.
        final_state = _make_final_state(message_id=234, title_needed=True)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with nullcontext():
            await client.post(
                "/chat/stream",
                json={
                    "room_id": 1,
                    "message_id": 234,
                    "message": "수영장 알려줘",
                    "title_needed": True,
                },
            )

        assert captured[0]["title_needed"] is True

    async def test_title_needed_false_when_request_flag_false(
        self, client: AsyncClient, mock_graph
    ):
        """요청의 title_needed=false → AgentState.title_needed=False. message_id 무관."""
        # message_id=1 이어도 플래그가 false 면 제목 미생성(추측 제거).
        final_state = _make_final_state(message_id=1, title_needed=False)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with nullcontext():
            await client.post(
                "/chat/stream",
                json={
                    "room_id": 1,
                    "message_id": 1,
                    "message": "수영장 알려줘",
                    "title_needed": False,
                },
            )

        assert captured[0]["title_needed"] is False

    async def test_title_needed_defaults_false_when_omitted(
        self, client: AsyncClient, mock_graph
    ):
        """title_needed 미전송(구 클라이언트) → False 기본값(하위호환)."""
        final_state = _make_final_state(message_id=1, title_needed=False)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with nullcontext():
            await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert captured[0]["title_needed"] is False

    async def test_prev_entities_injected_into_state(
        self, client: AsyncClient, mock_graph
    ):
        """ChatRequest.prev_entities/prev_intent/prev_reasoning → AgentState 주입."""
        final_state = _make_final_state(message_id=2)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        await client.post(
            "/chat/stream",
            json={
                "room_id": 1,
                "message_id": 2,
                "message": "이 곳 어떤 곳이야?",
                "prev_entities": [
                    {"service_id": "S1", "label": "마루공원 테니스장"},
                    {"service_id": "S2", "label": "강남 수영장"},
                ],
                "prev_intent": "VECTOR_SEARCH",
                "prev_reasoning": "직전에 자연 친화 시설로 분류함",
            },
        )

        st = captured[0]
        assert st["prev_entities"] == [
            {"service_id": "S1", "label": "마루공원 테니스장"},
            {"service_id": "S2", "label": "강남 수영장"},
        ]
        assert st["prev_intent"] == IntentType.VECTOR_SEARCH
        assert st["prev_reasoning"] == "직전에 자연 친화 시설로 분류함"
        assert st["target_service_ids"] is None

    async def test_prev_fields_default_when_omitted(
        self, client: AsyncClient, mock_graph
    ):
        """하위호환: 신규 필드 미전송 시 빈 배열/None 으로 주입(기존 동작)."""
        final_state = _make_final_state(message_id=2)
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        await client.post(
            "/chat/stream",
            json={"room_id": 1, "message_id": 2, "message": "수영장 알려줘"},
        )

        st = captured[0]
        assert st["prev_entities"] == []
        assert st["prev_intent"] is None
        assert st["prev_reasoning"] is None
        assert st["target_service_ids"] is None

    # 스트림 예외 → error 이벤트(정확히 1개)는 test_error_stream_yields_exactly_one_event 가,
    # 메시지 일반화/내부정보 미노출은 test_error_event_message_is_generic 이 커버하므로
    # 동일 분기의 예외 타입 순열로 축소했다.

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

        with nullcontext():
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

    # lng 범위 422 는 test_invalid_lat_returns_422 와 동일한 좌표 범위 validator의
    # 값만 다른 순열이라 축소했다(lat 케이스 + 경계 케이스로 분기 커버 유지).

    async def test_boundary_lat_exactly_90_is_valid(
        self, client: AsyncClient, mock_graph
    ):
        """lat=90.0 경계값은 유효하므로 422가 아니어야 한다."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
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

        with nullcontext():
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

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 2, "message": "테스트"},
            )

        events = _parse_sse_events(response.content)
        assert len(events) == 1
        assert events[0]["event"] == "error"

    async def test_rate_limit_exception_yields_rate_limit_error_event(
        self, client: AsyncClient, mock_graph
    ):
        """RateLimitException 발생 시 error 이벤트와 rate-limit 안내 메시지가 반환된다."""
        mock_graph.stream = MagicMock(
            side_effect=RateLimitException("Gemini embed rate limit 소진")
        )

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        error_events = [e for e in events if e.get("event") == "error"]
        assert len(error_events) == 1
        assert (
            error_events[0]["data"]["message"]
            == "현재 요청이 많아 잠시 후 다시 시도해 주세요."
        )

    # rate-limit 메시지가 범용 error 메시지와 다르다는 단언은 위
    # test_rate_limit_exception_yields_rate_limit_error_event 의 정확한 문자열 매칭이
    # 이미 함의하므로(서로 다른 고정 문자열) 축소했다.

    async def test_error_event_message_is_generic(
        self, client: AsyncClient, mock_graph
    ):
        """error 이벤트의 message 필드는 예외 내용을 노출하지 않고 범용 문자열을 반환한다."""
        mock_graph.stream = MagicMock(side_effect=RuntimeError("LLM 타임아웃 발생"))

        with nullcontext():
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

    async def test_final_event_has_no_title(self, client: AsyncClient, mock_graph):
        """제목은 별도 title 이벤트로 분리됐다 — final payload 에 title 키가 없다."""
        final_state = _make_final_state(message_id=1, title_needed=True)
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert "title" not in final_events[0]["data"]

    async def test_title_event_relayed_to_sse(self, client: AsyncClient, mock_graph):
        """graph.stream 이 yield 한 title 이벤트가 SSE 프레임으로 릴레이된다."""
        final_state = _make_final_state(message_id=1, title_needed=True)

        async def _gen(state, **kwargs):
            yield "progress", {"step": "routing", "message": "..."}
            yield (
                "title",
                {
                    "type": "title",
                    "room_id": 1,
                    "title": "수영장 문의",
                    "message_id": 1,
                    "query": "수영장 알려줘",
                },
            )
            yield "result", final_state

        mock_graph.stream = _gen

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        title_events = [e for e in events if e["event"] == "title"]
        assert len(title_events) == 1
        data = title_events[0]["data"]
        assert data["type"] == "title"
        assert data["title"] == "수영장 문의"
        assert data["query"] == "수영장 알려줘"

    async def test_missing_required_field_returns_422(self, client: AsyncClient):
        """필수 필드 누락(message 없음) → 422 반환.

        room_id 누락 등 다른 필수 필드 순열도 동일 required-field validator 분기라
        대표 케이스 하나만 유지한다.
        """
        response = await client.post(
            "/chat/stream",
            json={"room_id": 1, "message_id": 1},
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

        with nullcontext():
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

        with nullcontext():
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
