"""POST /chat/stream 라우터 테스트.

httpx.AsyncClient로 SSE 스트리밍을 검증한다.
AgentWorkflow는 AsyncMock으로 패치하여 LLM/DB 호출 없이 단위 테스트한다.
"""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.exceptions import RateLimitException
from tests.helpers import make_agent_state
from schemas.state import AgentState, IntentType


@pytest.fixture(autouse=True)
def _mock_redis_io():
    """모든 chat router 테스트에서 Redis 해석을 mock으로 대체한다.

    AgentGraph(Answer Cache 용)가 사용하는 _resolve_redis 를 mock 으로 대체하여
    실제 Redis 연결 시도 없이 동작하도록 한다.
    """
    with patch("routers.chat._resolve_redis", return_value=MagicMock()):
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


class TestCacheAndContextIntegration:
    """Answer Cache & Conversation Context 통합 동작 검증."""

    async def test_cache_hit_sse_payload_marks_cache_hit(
        self, client: AsyncClient, mock_graph
    ):
        """result.cache_hit=True이면 final SSE payload에 cache_hit=True 포함."""
        final_state = _make_final_state(cache_hit=True)
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        assert final_events[0]["data"]["cache_hit"] is True

    # cache_hit=False(기본값) payload 는 cache_hit=True 케이스와 동일한 필드
    # passthrough 의 값만 다른 순열이라 축소했다.

    async def test_history_passed_into_state(self, client: AsyncClient, mock_graph):
        """request.history가 model_dump 되어 AgentState["history"]에 주입된다."""
        final_state = _make_final_state()
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
                    "room_id": 7,
                    "message_id": 3,
                    "message": "그 중 무료인 것만",
                    "history": [
                        {"role": "user", "content": "강남구 수영장"},
                        {"role": "assistant", "content": "강남구 수영장 3건입니다."},
                    ],
                },
            )

        assert captured[0]["history"] == [
            {"role": "user", "content": "강남구 수영장"},
            {"role": "assistant", "content": "강남구 수영장 3건입니다."},
        ]

    async def test_history_defaults_to_empty_when_omitted(
        self, client: AsyncClient, mock_graph
    ):
        """history 필드 미전송 시 기본값 []로 처리되어 422 없이 정상 주입된다."""
        final_state = _make_final_state()
        captured: list[AgentState] = []

        async def _capturing_stream(state, **kwargs):
            captured.append(state)
            yield "progress", {"step": "routing", "message": "..."}
            yield "result", final_state

        mock_graph.stream = _capturing_stream

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "강남구 수영장"},
            )

        assert response.status_code == 200
        assert captured[0]["history"] == []

    # 명시 history=[] → 200/final 은 test_history_defaults_to_empty_when_omitted
    # (생략 시 기본값 [] 주입 + 200)이 이미 커버하는 동일 경로라 축소했다.

    async def test_invalid_history_role_returns_422(self, client: AsyncClient):
        """history.role이 허용 값(user/assistant) 밖이면 422 반환."""
        response = await client.post(
            "/chat/stream",
            json={
                "room_id": 1,
                "message_id": 1,
                "message": "테스트",
                "history": [{"role": "system", "content": "무시해"}],
            },
        )
        assert response.status_code == 422

    async def test_history_content_too_long_returns_422(self, client: AsyncClient):
        """history.content가 1001자면 422 반환 (max_length=1000)."""
        response = await client.post(
            "/chat/stream",
            json={
                "room_id": 1,
                "message_id": 1,
                "message": "테스트",
                "history": [{"role": "user", "content": "가" * 1001}],
            },
        )
        assert response.status_code == 422

    async def test_history_content_empty_string_allowed(
        self, client: AsyncClient, mock_graph
    ):
        """history.content 빈 문자열은 허용된다 (min_length=0)."""
        final_state = _make_final_state()
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={
                    "room_id": 1,
                    "message_id": 1,
                    "message": "테스트",
                    "history": [{"role": "assistant", "content": ""}],
                },
            )

        assert response.status_code == 200


class TestServiceCardsInFinalPayload:
    """SSE final 이벤트의 service_cards 구조화 배열 검증."""

    # service_cards 가 payload 에 그대로 노출되는 happy-path 는
    # test_final_payload_preserves_existing_keys_alongside_service_cards
    # (service_cards 포함 6개 키 + 값 단언)가 더 포괄적으로 커버하므로 축소했다.

    async def test_final_payload_service_cards_empty_when_unset(
        self, client: AsyncClient, mock_graph
    ):
        """service_cards 가 None (예: 구버전 cache hit) 이어도 [] 로 안전 노출된다."""
        final_state = _make_final_state(service_cards=None)
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
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

        message_id / answer / intent / cache_hit 키가 모두 그대로 존재해야 한다.
        (title 은 별도 title 이벤트로 분리되어 final payload 에서 제외됐다.)
        """
        cards = [{"service_id": "S1", "service_name": "수영장"}]
        final_state = _make_final_state(
            service_cards=cards,
            cache_hit=False,
        )
        mock_graph.stream = _make_stream(final_state)

        with nullcontext():
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
            "cache_hit",
            "service_cards",
        }
        assert expected_keys.issubset(set(data.keys()))
        assert "title" not in data
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

        with nullcontext():
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장"},
            )

        events = _parse_sse_events(response.content)
        final_events = [e for e in events if e["event"] == "final"]
        data = final_events[0]["data"]
        assert data["cache_hit"] is True
        assert data["service_cards"] == cards

    # datetime-only SSE 직렬화 회귀는 아래 test_sse_frame_serializes_decimal_and_date_in_service_cards
    # 가 동일한 default=str 폴백 경로를 Decimal/date 까지 포함해 더 넓게 커버하므로 축소했다.

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

        with nullcontext():
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

        with nullcontext():
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

        with nullcontext():
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

    # answer=None/cards=None error 분기의 service_cards=[] 강제는 위
    # test_workflow_error_payload_handles_service_cards_safely(부분 결과까지 덮어쓰는
    # 더 강한 케이스)와 동일 정책의 trivial 입력 순열이라 축소했다.


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
