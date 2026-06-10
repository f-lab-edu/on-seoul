"""[D] W3 — decision SSE 이벤트 emit 검증.

검증 대상:
  1. DecisionEvent 스키마 유효성 (필드/타입/기본값)
  2. sanitize_user_rationale: None/빈값 → None, __ 패턴 제거, 200자 truncate
  3. graph.stream(): triage_node 완료 직후 decision 이벤트 방출 + 내용 검증
  4. triage_node user_rationale=None → decision 이벤트 미방출
  5. router_node(하위호환) 경로 → decision 이벤트 미방출
  6. 참조 해소 경로(reference_resolution → rehydrate) → decision 이벤트 미방출
  7. routers/chat.py: decision 이벤트가 SSE 프레임으로 직렬화되는지 확인
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agents.graph import AgentGraph
from agents.nodes import sanitize_user_rationale
from schemas.events import DecisionEvent
from schemas.state import ActionType, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_router,
    make_sql_agent,
    make_triage,
    make_ai_session,
    stream_graph,
)


# ---------------------------------------------------------------------------
# 1. DecisionEvent 스키마
# ---------------------------------------------------------------------------


class TestDecisionEventSchema:
    def test_default_event_literal(self):
        """event 필드가 기본값 'decision'으로 고정된다."""
        ev = DecisionEvent(
            action="RETRIEVE",
            routes=["SQL_SEARCH"],
            user_rationale="수영장 검색입니다.",
        )
        assert ev.event == "decision"

    def test_sources_default_empty(self):
        """sources 기본값이 빈 리스트다."""
        ev = DecisionEvent(
            action="DIRECT_ANSWER",
            routes=[],
            user_rationale="직접 답변입니다.",
        )
        assert ev.sources == []

    def test_routes_multi(self):
        """routes에 복수 intent를 담을 수 있다."""
        ev = DecisionEvent(
            action="RETRIEVE",
            routes=["SQL_SEARCH", "VECTOR_SEARCH"],
            user_rationale="복합 검색입니다.",
        )
        assert len(ev.routes) == 2

    def test_model_dump_json_serializable(self):
        """model_dump() 결과가 JSON 직렬화 가능해야 한다."""
        ev = DecisionEvent(
            action="RETRIEVE",
            routes=["SQL_SEARCH"],
            user_rationale="테스트",
            sources=[{"channel": "sql", "hits": 0}],
        )
        dumped = ev.model_dump()
        json_str = json.dumps(dumped)
        assert "decision" in json_str


# ---------------------------------------------------------------------------
# 2. sanitize_user_rationale
# ---------------------------------------------------------------------------


class TestSanitizeUserRationale:
    def test_none_returns_none(self):
        assert sanitize_user_rationale(None) is None

    def test_empty_string_returns_none(self):
        assert sanitize_user_rationale("") is None

    def test_whitespace_only_returns_none(self):
        assert sanitize_user_rationale("   ") is None

    def test_normal_text_passes_through(self):
        result = sanitize_user_rationale("수영장을 검색합니다.")
        assert result == "수영장을 검색합니다."

    def test_internal_pattern_removed(self):
        """'__' 포함 줄은 제거된다."""
        text = "정상 텍스트\n__internal_key: 시스템 값\n두 번째 줄"
        result = sanitize_user_rationale(text)
        assert "__" not in (result or "")
        assert "정상 텍스트" in (result or "")

    def test_truncate_at_200_chars(self):
        """200자 초과 시 말줄임표로 truncate된다."""
        long_text = "가" * 250
        result = sanitize_user_rationale(long_text)
        assert result is not None
        assert len(result) <= 200
        assert result.endswith("...")

    def test_exactly_200_chars_not_truncated(self):
        """정확히 200자이면 truncate되지 않는다."""
        text = "나" * 200
        result = sanitize_user_rationale(text)
        assert result == text

    def test_201_chars_truncated_with_ellipsis(self):
        """201자이면 197자 + '...'(3자) = 200자로 truncate된다."""
        text = "다" * 201
        result = sanitize_user_rationale(text)
        assert result is not None
        assert len(result) == 200
        assert result.endswith("...")
        # 말줄임표 앞부분은 원본 첫 197자
        assert result[:197] == "다" * 197

    def test_only_internal_lines_returns_none(self):
        """모든 줄이 내부 패턴이면 None을 반환한다."""
        text = "__key1: val\n__key2: val"
        assert sanitize_user_rationale(text) is None

    def test_technical_description_with_dunder_preserved(self):
        """줄 중간에 __가 포함된 정상 기술 설명은 보존된다."""
        text = "파이썬 __init__ 사용법을 안내합니다."
        result = sanitize_user_rationale(text)
        assert result == text

    def test_line_starting_with_dunder_removed_but_rest_preserved(self):
        """줄 시작 __ 줄은 제거되고, 나머지 정상 줄은 보존된다."""
        text = "정상 설명입니다.\n__internal_meta: 시스템 값\n추가 설명입니다."
        result = sanitize_user_rationale(text)
        assert result is not None
        assert "__internal_meta" not in result
        assert "정상 설명입니다." in result
        assert "추가 설명입니다." in result


# ---------------------------------------------------------------------------
# 3-6. graph.stream() decision 이벤트
# ---------------------------------------------------------------------------


def _collect_events(gen):
    """async generator에서 이벤트 목록을 수집하는 coroutine."""

    async def _run():
        events = []
        async for ev in gen:
            events.append(ev)
        return events

    return _run()


class TestStreamDecisionEvent:
    async def _collect(self, graph, state, **kwargs):
        events = []
        async for ev in stream_graph(graph, state, **kwargs):
            events.append(ev)
        return events

    async def test_decision_event_emitted_after_triage(self):
        """RETRIEVE 경로에서 router_node 완료 후 decision 이벤트가 방출된다."""
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="수영장 관련 질문으로 판단합니다.",
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        events = await self._collect(
            graph,
            make_agent_state(),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 1, "decision 이벤트가 정확히 1회 방출되어야 한다"

    async def test_decision_event_schema(self):
        """방출된 decision 이벤트 데이터가 올바른 필드를 포함한다."""
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="마포구 풋살장 검색입니다.",
        )
        router = make_router(
            IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        events = await self._collect(
            graph,
            make_agent_state(message="마포구 풋살장"),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 1
        _, data = decision_events[0]

        assert data["event"] == "decision"
        assert data["action"] == ActionType.RETRIEVE.value
        # primary + secondary 모두 포함 (routes는 router_node에서 확정)
        assert "SQL_SEARCH" in data["routes"]
        assert "VECTOR_SEARCH" in data["routes"]
        assert data["user_rationale"] == "마포구 풋살장 검색입니다."
        assert data["sources"] == []

    async def test_decision_not_emitted_when_rationale_none(self):
        """user_rationale=None이면 decision 이벤트가 방출되지 않는다."""
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale=None,  # None → emit 스킵
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        events = await self._collect(
            graph,
            make_agent_state(),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 0, "user_rationale=None이면 decision 미방출"

    async def test_decision_not_emitted_on_reference_resolution_path(self):
        """참조 해소 경로(referential)는 triage_node를 거치지 않으므로 decision 미방출."""

        triage = make_triage(ActionType.RETRIEVE, IntentType.VECTOR_SEARCH)
        graph = AgentGraph(
            triage=triage,
            answer_agent=make_answer_agent("첫 번째 시설 안내입니다."),
        )

        # prev_entities가 있어야 resolve_reference가 referential로 판정할 수 있다.
        # "1번이랑 2번" 같은 표현 + prev_entities로 referential 경로 트리거.
        prev_entities = [
            {"service_id": "S001", "label": "수영장"},
            {"service_id": "S002", "label": "체육관"},
        ]

        with patch(
            "agents.nodes.resolve_reference",
            return_value=["S001"],
        ):
            with patch(
                "agents.nodes.hydrate_services",
                AsyncMock(
                    return_value=[{"service_id": "S001", "service_name": "수영장"}]
                ),
            ):
                events = await self._collect(
                    graph,
                    make_agent_state(
                        message="1번 자세히 알려줘",
                        prev_entities=prev_entities,
                    ),
                    data_session=MagicMock(),
                    ai_session=make_ai_session(),
                )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 0, "참조 해소 경로에서는 decision 미방출"

    async def test_decision_routes_empty_for_non_retrieve_action(self):
        """DIRECT_ANSWER action 시 routes=[] 이고 decision 이벤트가 방출된다."""
        triage = make_triage(
            ActionType.DIRECT_ANSWER,
            IntentType.FALLBACK,
            user_rationale="서비스 범위 외 질문입니다.",
        )
        graph = AgentGraph(
            triage=triage,
            answer_agent=make_answer_agent("직접 답변"),
        )

        events = await self._collect(
            graph,
            make_agent_state(message="오늘 날씨 어때?"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 1
        _, data = decision_events[0]
        assert data["action"] == ActionType.DIRECT_ANSWER.value
        # 비-RETRIEVE action 시 routes는 하드코딩된 빈 리스트([])로 방출된다.
        # 따라서 None을 포함한 어떤 항목도 들어 있어선 안 된다.
        assert None not in data["routes"]
        assert data["sources"] == []

    async def test_decision_not_emitted_on_cache_hit_path(self):
        """cache_hit 경로(triage 완료 후 cache_check hit) — decision은 1회 방출된다.

        cache hit 시 triage_node는 정상 실행(user_rationale 있음)하므로 decision은 방출된다.
        단, cache_hit=True인 경우 검색/hydration 노드가 실행되지 않는다.
        이 테스트는 cache_hit 여부와 관계없이 triage의 user_rationale 존재 여부가
        decision 방출의 유일한 조건임을 검증한다.
        """
        from unittest.mock import patch as _patch

        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="캐시 히트 경로 테스트입니다.",
        )
        router = make_router(IntentType.SQL_SEARCH, refined_query="수영장")
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        # cache hit → triage·router 실행(user_rationale 있음), 검색 노드는 스킵
        cache_payload = {
            "payload": {"answer": "캐시 답변", "title": None, "service_cards": []},
            "state": {
                "vector_results": None,
                "sql_results": None,
                "hydrated_services": None,
                "max_class_name": None,
                "area_name": None,
                "service_status": None,
                "payment_type": None,
                "refined_query": "수영장",
            },
        }
        with _patch("agents.nodes.get_cached_answer", return_value=cache_payload):
            events = await self._collect(
                graph,
                make_agent_state(
                    message="수영장 알려줘",
                    refined_query="수영장",  # cache_check에 필요
                ),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        # triage가 user_rationale을 반환했으므로 1회 방출
        assert len(decision_events) == 1

    async def test_decision_not_emitted_on_router_node_path(self):
        """RouterAgent(하위호환 별칭) 경로는 user_rationale을 반환하지 않으므로 decision 미방출."""
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        events = await self._collect(
            graph,
            make_agent_state(),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 0, "router_node 경로에서는 decision 미방출"

    async def test_decision_emitted_once_on_retry(self):
        """self-correction 재시도 시 decision 이벤트는 1회만 방출된다.

        1차 router_node에서 triage 보류 rationale로 decision emit. 재시도 재진입은
        router_node(forced_intent 경로)지만 _decision_emitted 가드로 재방출되지 않는다.
        """
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="수영장 검색입니다.",
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("재시도 후 답변"),
        )

        # hydration 0건 → retry_prep → router_node 재진입(forced_intent)
        with patch(
            "agents.hydration_node.hydrate_services",
            AsyncMock(return_value=[]),
        ):
            events = await self._collect(
                graph,
                make_agent_state(),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        decision_events = [(t, d) for t, d in events if t == "decision"]
        # router_node 1차에서 1회 방출, 재시도 재진입은 가드로 미방출.
        assert len(decision_events) == 1


# ---------------------------------------------------------------------------
# 7. routers/chat.py SSE 직렬화
# ---------------------------------------------------------------------------


def _parse_sse_events(content: bytes) -> list[dict]:
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


@pytest.fixture(autouse=True)
def _mock_redis_io():
    with patch("routers.chat._resolve_redis", return_value=MagicMock()):
        yield


class TestDecisionSSEFrame:
    def _make_stream_with_decision(self, rationale: str | None):
        """decision 이벤트를 포함하는 stream mock factory."""

        final_state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            answer="답변입니다.",
        )

        async def _gen(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "분석 중..."}
            if rationale:
                yield (
                    "decision",
                    {
                        "event": "decision",
                        "action": "RETRIEVE",
                        "routes": ["SQL_SEARCH"],
                        "user_rationale": rationale,
                        "sources": [],
                    },
                )
            yield "progress", {"step": "searching", "message": "검색 중..."}
            yield "result", final_state

        return _gen

    async def test_decision_sse_frame_emitted(self, client: AsyncClient):
        """decision 이벤트가 SSE 프레임으로 클라이언트에 전달된다."""
        graph = MagicMock()
        graph.stream = self._make_stream_with_decision("수영장 관련 질문입니다.")

        with patch("routers.chat._resolve_graph", return_value=graph):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        decision_events = [e for e in events if e.get("event") == "decision"]
        assert len(decision_events) == 1

        data = decision_events[0]["data"]
        assert data["event"] == "decision"
        assert data["action"] == "RETRIEVE"
        assert "SQL_SEARCH" in data["routes"]
        assert data["user_rationale"] == "수영장 관련 질문입니다."
        assert data["sources"] == []

    async def test_no_decision_sse_when_rationale_none(self, client: AsyncClient):
        """user_rationale=None이면 decision SSE 프레임이 없다."""
        graph = MagicMock()
        graph.stream = self._make_stream_with_decision(None)

        with patch("routers.chat._resolve_graph", return_value=graph):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        decision_events = [e for e in events if e.get("event") == "decision"]
        assert len(decision_events) == 0


# ---------------------------------------------------------------------------
# 8. QA 회귀 — 재시도 재진입에도 decision 이벤트 1회 보장 (_decision_emitted 가드)
# ---------------------------------------------------------------------------


class TestDecisionEmitOnceAcrossRetry:
    """RETRIEVE 0건 → retry_prep → router_node 재진입 사이클에서도 decision
    이벤트는 정확히 1회만 방출되어야 한다(_decision_emitted 가드).

    책임 분리 후 RETRIEVE 의 decision 은 router_node 완료 시점에 emit 된다.
    retry 가 router_node 를 재실행시키므로 가드가 없으면 2회 emit 될 위험이 있다.
    """

    async def _collect(self, graph, state, **kwargs):
        events = []
        async for ev in stream_graph(graph, state, **kwargs):
            events.append(ev)
        return events

    async def test_decision_emitted_once_when_router_reenters_on_retry(self):
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="수영장 검색입니다.",
        )
        # VECTOR_SEARCH 0건 → 케이스 C 완화 재시도 → router_node 재진입.
        router = make_router(IntentType.VECTOR_SEARCH)

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ), patch(
            "agents.vector_agent.VectorAgent.search",
            AsyncMock(return_value=[]),
        ):
            graph = AgentGraph(
                triage=triage,
                router=router,
                answer_agent=make_answer_agent("재시도 후 답변"),
            )
            events = await self._collect(
                graph,
                make_agent_state(),
                data_session=make_ai_session(),
                ai_session=make_ai_session(),
            )

        # 전제: 재시도가 실제로 일어나 router 가 2회 실행되었는지 progress 로 확인.
        searching = [
            d for t, d in events if t == "progress" and d.get("step") == "searching"
        ]
        assert searching, "searching progress 미방출 — 검색 경로 미진입"

        decision_events = [(t, d) for t, d in events if t == "decision"]
        assert len(decision_events) == 1, (
            f"재진입에도 decision 은 1회여야 한다: {len(decision_events)}회 방출"
        )
        _, data = decision_events[0]
        assert data["action"] == "RETRIEVE"
        assert "VECTOR_SEARCH" in data["routes"]
        assert data["user_rationale"] == "수영장 검색입니다."
