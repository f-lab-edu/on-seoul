"""sources_update 이벤트 검증.

검증 대상:
  1. SourcesUpdateEvent 스키마 유효성
  2. _build_sources: sql/vector/map/analytics 채널별 hits 추출, 빈 채널 제외
  3. graph.stream(): yield "result" 직전 sources_update 이벤트 emit
     - 검색 결과 있으면 채널별 hits 포함
     - 빈 채널은 제외 (None/[] 모두)
     - cache_hit=True 경로에서도 state 복원값 기반으로 동작
  4. routers/chat.py: sources_update SSE 프레임 직렬화
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agents.graph import AgentGraph, _build_sources
from schemas.events import SourceEntry, SourcesUpdateEvent
from schemas.state import ActionType, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_ai_session,
    make_router,
    make_sql_agent,
    make_triage,
    stream_graph,
)


# ---------------------------------------------------------------------------
# 1. SourcesUpdateEvent 스키마
# ---------------------------------------------------------------------------


class TestSourcesUpdateEventSchema:
    def test_default_event_literal(self):
        ev = SourcesUpdateEvent(sources=[])
        assert ev.event == "sources_update"

    def test_sources_field(self):
        ev = SourcesUpdateEvent(sources=[{"channel": "sql", "hits": 3}])
        assert len(ev.sources) == 1
        assert isinstance(ev.sources[0], SourceEntry)
        assert ev.sources[0].channel == "sql"
        assert ev.sources[0].hits == 3

    def test_model_dump_json_serializable(self):
        ev = SourcesUpdateEvent(
            sources=[{"channel": "sql", "hits": 5}, {"channel": "vector", "hits": 2}]
        )
        dumped = ev.model_dump()
        json_str = json.dumps(dumped)
        assert "sources_update" in json_str
        assert "sql" in json_str


# ---------------------------------------------------------------------------
# 2. _build_sources 유닛 테스트
# ---------------------------------------------------------------------------


class TestBuildSources:
    def test_all_none_returns_empty(self):
        state = {"sql": {"results": None}, "vector": {"results": None}, "map": {"results": None}}
        assert _build_sources(state) == []

    def test_all_empty_list_returns_empty(self):
        state = {"sql": {"results": []}, "vector": {"results": []}, "map": {"results": None}}
        assert _build_sources(state) == []

    def test_sql_results_included(self):
        state = {
            "sql": {"results": [{"service_id": "S1"}, {"service_id": "S2"}]},
            "vector": {"results": None},
            "map": {"results": None},
        }
        sources = _build_sources(state)
        assert len(sources) == 1
        assert sources[0] == {"channel": "sql", "hits": 2}

    def test_vector_results_included(self):
        state = {
            "sql": {"results": None},
            "vector": {"results": [{"service_id": "V1"}]},
            "map": {"results": None},
        }
        sources = _build_sources(state)
        assert len(sources) == 1
        assert sources[0] == {"channel": "vector", "hits": 1}

    def test_map_results_with_features(self):
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": {
                "type": "FeatureCollection",
                "features": [{"id": 1}, {"id": 2}, {"id": 3}],
            }},
        }
        sources = _build_sources(state)
        assert len(sources) == 1
        assert sources[0] == {"channel": "map", "hits": 3}

    def test_map_results_without_features_key_hits_1(self):
        """features 키 없는 map_results dict는 hits=1로 처리한다."""
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": {"type": "FeatureCollection"}},
        }
        sources = _build_sources(state)
        assert len(sources) == 1
        assert sources[0]["channel"] == "map"
        assert sources[0]["hits"] == 1

    def test_multiple_channels_all_included(self):
        state = {
            "sql": {"results": [{"service_id": "S1"}]},
            "vector": {"results": [{"service_id": "V1"}, {"service_id": "V2"}]},
            "map": {"results": None},
        }
        sources = _build_sources(state)
        channels = {s["channel"] for s in sources}
        assert channels == {"sql", "vector"}
        hits_by_channel = {s["channel"]: s["hits"] for s in sources}
        assert hits_by_channel["sql"] == 1
        assert hits_by_channel["vector"] == 2

    def test_missing_keys_returns_empty(self):
        """AgentState 슬롯이 아예 없는 dict도 빈 리스트를 반환한다."""
        assert _build_sources({}) == []

    def test_map_results_empty_features_excluded(self):
        """features=[] 인 map_results는 hits=0이므로 채널에서 제외된다."""
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": {"type": "FeatureCollection", "features": []}},
        }
        sources = _build_sources(state)
        assert sources == []

    def test_analytics_results_included(self):
        """analytics_results가 있으면 analytics 채널이 포함된다."""
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": None},
            "analytics": {"results": [
                {"group_value": "강남구", "count": 10},
                {"group_value": "송파구", "count": 7},
            ]},
        }
        sources = _build_sources(state)
        assert len(sources) == 1
        assert sources[0] == {"channel": "analytics", "hits": 2}

    def test_analytics_results_empty_list_excluded(self):
        """analytics_results=[] 이면 채널에서 제외된다."""
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": None},
            "analytics": {"results": []},
        }
        assert _build_sources(state) == []

    def test_analytics_results_none_excluded(self):
        """analytics_results=None 이면 채널에서 제외된다."""
        state = {
            "sql": {"results": None},
            "vector": {"results": None},
            "map": {"results": None},
            "analytics": {"results": None},
        }
        assert _build_sources(state) == []


# ---------------------------------------------------------------------------
# 3. graph.stream() sources_update emit
# ---------------------------------------------------------------------------


class TestStreamSourcesUpdateEvent:
    async def _collect(self, graph, state, **kwargs):
        events = []
        async for ev in stream_graph(graph, state, **kwargs):
            events.append(ev)
        return events

    async def test_sources_update_emitted_with_sql_results(self):
        """SQL 검색 결과가 있으면 sources_update 이벤트가 sql 채널 hits를 담아 방출된다."""
        triage = make_triage(ActionType.RETRIEVE, user_rationale="수영장 검색입니다.")
        router = make_router(IntentType.SQL_SEARCH)
        sql_rows = [{"service_id": "S1", "service_name": "수영장A"}]
        sql_agent, data_session = make_sql_agent(sql_rows)
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

        su_events = [(t, d) for t, d in events if t == "sources_update"]
        assert len(su_events) == 1
        _, data = su_events[0]
        assert data["event"] == "sources_update"
        sources = data["sources"]
        assert len(sources) == 1
        assert sources[0]["channel"] == "sql"
        assert sources[0]["hits"] == 1

    async def test_sources_update_not_emitted_when_no_results(self):
        """검색 결과가 없으면 sources_update 이벤트가 방출되지 않는다."""
        triage = make_triage(ActionType.RETRIEVE, user_rationale="수영장 검색입니다.")
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

        su_events = [(t, d) for t, d in events if t == "sources_update"]
        assert len(su_events) == 0

    async def test_sources_update_emitted_before_result(self):
        """sources_update 이벤트는 result 이벤트 직전에 방출된다."""
        triage = make_triage(ActionType.RETRIEVE, user_rationale="수영장 검색입니다.")
        router = make_router(IntentType.SQL_SEARCH)
        sql_rows = [{"service_id": "S1", "service_name": "수영장A"}]
        sql_agent, data_session = make_sql_agent(sql_rows)
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

        event_types = [t for t, _ in events]
        su_idx = event_types.index("sources_update")
        result_idx = event_types.index("result")
        assert su_idx < result_idx, "sources_update는 result 직전이어야 한다"
        assert su_idx == result_idx - 1, "sources_update와 result 사이에 다른 이벤트가 없어야 한다"

    async def test_sources_update_on_cache_hit_path(self):
        """cache_hit=True 경로에서도 state 복원값 기반으로 sources_update가 방출된다."""
        triage = make_triage(ActionType.RETRIEVE, user_rationale="캐시 히트 테스트입니다.")
        router = make_router(IntentType.SQL_SEARCH, refined_query="수영장")
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
        )

        # cache hit — state에 sql_results가 복원됨
        cache_payload = {
            "payload": {"answer": "캐시 답변", "title": None, "service_cards": []},
            "state": {
                "sql_results": [{"service_id": "S1"}],
                "vector_results": None,
                "hydrated_services": None,
                "max_class_name": None,
                "area_name": None,
                "service_status": None,
                "payment_type": None,
                "refined_query": "수영장",
            },
        }
        with patch("agents.nodes.get_cached_answer_by_key", return_value=cache_payload):
            events = await self._collect(
                graph,
                make_agent_state(message="수영장 알려줘", refined_query="수영장"),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        su_events = [(t, d) for t, d in events if t == "sources_update"]
        assert len(su_events) == 1
        _, data = su_events[0]
        assert data["sources"][0]["channel"] == "sql"
        assert data["sources"][0]["hits"] == 1


# ---------------------------------------------------------------------------
# 4. routers/chat.py SSE 직렬화
# ---------------------------------------------------------------------------


def _parse_sse_events(content: bytes) -> list[dict]:
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


class TestSourcesUpdateSSEFrame:
    def _make_stream_with_sources(self, sources: list[dict]):
        final_state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            answer="답변입니다.",
        )

        async def _gen(*args, **kwargs):
            yield "progress", {"step": "routing", "message": "분석 중..."}
            if sources:
                yield (
                    "sources_update",
                    {
                        "event": "sources_update",
                        "sources": sources,
                    },
                )
            yield "result", final_state

        return _gen

    async def test_sources_update_sse_frame_emitted(self, client: AsyncClient):
        """sources_update 이벤트가 SSE 프레임으로 클라이언트에 전달된다."""
        graph = MagicMock()
        graph.stream = self._make_stream_with_sources(
            [{"channel": "sql", "hits": 3}]
        )

        with patch("routers.chat._resolve_graph", return_value=graph):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        assert response.status_code == 200
        events = _parse_sse_events(response.content)
        su_events = [e for e in events if e.get("event") == "sources_update"]
        assert len(su_events) == 1

        data = su_events[0]["data"]
        assert data["event"] == "sources_update"
        assert data["sources"] == [{"channel": "sql", "hits": 3}]

    async def test_no_sources_update_sse_when_empty(self, client: AsyncClient):
        """sources가 빈 리스트면 sources_update SSE 프레임이 없다."""
        graph = MagicMock()
        graph.stream = self._make_stream_with_sources([])

        with patch("routers.chat._resolve_graph", return_value=graph):
            response = await client.post(
                "/chat/stream",
                json={"room_id": 1, "message_id": 1, "message": "수영장 알려줘"},
            )

        events = _parse_sse_events(response.content)
        su_events = [e for e in events if e.get("event") == "sources_update"]
        assert len(su_events) == 0
