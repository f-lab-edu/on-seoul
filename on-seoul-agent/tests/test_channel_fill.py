"""Task 4 — 노드별 ChannelData 채움 단위 테스트.

sql_node / vector_node / map_node 가 각자 search_channels 에 올바른 ChannelData 를
채우는지, _to_hits 헬퍼가 rank·score·meta 를 올바르게 변환하는지 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents._search_channel_utils import _to_hits
from agents.nodes import GraphNodes
from schemas.search import (
    ChannelHit,
    SearchChannel,
    SearchKind,
)
from tests.helpers import make_agent_state


# ---------------------------------------------------------------------------
# 헬퍼 — nodes 인스턴스 생성
# ---------------------------------------------------------------------------


def _make_nodes(
    sql_agent=None,
    vector_agent=None,
    answer_agent=None,
) -> GraphNodes:
    nodes = GraphNodes(
        router=MagicMock(),
        sql_agent=sql_agent or MagicMock(),
        vector_agent=vector_agent or MagicMock(),
        answer_agent=answer_agent or MagicMock(),
    )
    return nodes


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.execute = AsyncMock(return_value=MagicMock())
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# _to_hits 헬퍼
# ---------------------------------------------------------------------------


class TestToHits:
    def test_assigns_1_based_rank(self):
        rows = [
            {"service_id": "A", "similarity": 0.9},
            {"service_id": "B", "similarity": 0.8},
        ]
        hits = _to_hits(rows, score_field="similarity")
        assert hits[0]["rank"] == 1
        assert hits[1]["rank"] == 2

    def test_extracts_score_field(self):
        rows = [{"service_id": "A", "bm25_score": 3.14}]
        hits = _to_hits(rows, score_field="bm25_score")
        assert hits[0]["score"] == pytest.approx(3.14)

    def test_score_none_when_field_missing(self):
        rows = [{"service_id": "A"}]
        hits = _to_hits(rows, score_field="similarity")
        assert hits[0]["score"] is None

    def test_score_none_when_score_field_is_none(self):
        rows = [{"service_id": "A", "rrf_score": 0.5}]
        hits = _to_hits(rows, score_field=None)
        assert hits[0]["score"] is None

    def test_meta_fn_called_per_row(self):
        rows = [
            {"service_id": "A", "distance_m": 100},
            {"service_id": "B", "distance_m": 200},
        ]
        hits = _to_hits(
            rows,
            score_field="distance_m",
            meta_fn=lambda r: {"distance_m": r["distance_m"]},
        )
        assert hits[0]["meta"]["distance_m"] == 100
        assert hits[1]["meta"]["distance_m"] == 200

    def test_empty_rows_returns_empty_list(self):
        assert _to_hits([], score_field="similarity") == []

    def test_score_field_value_none_gives_none(self):
        """score_field 키는 있지만 값이 None인 경우 score=None."""
        rows = [{"service_id": "A", "similarity": None}]
        hits = _to_hits(rows, score_field="similarity")
        assert hits[0]["score"] is None

    def test_meta_defaults_to_empty_dict(self):
        rows = [{"service_id": "A"}]
        hits = _to_hits(rows, score_field=None)
        assert hits[0]["meta"] == {}


# ---------------------------------------------------------------------------
# sql_node ChannelData
# ---------------------------------------------------------------------------


class TestSqlNodeChannelData:
    def _make_sql_agent(self, rows: list[dict], keyword: str | None = "수영장") -> MagicMock:
        """search() 가 sql_results + sql_keyword 를 반환하는 mock SqlAgent."""
        agent = MagicMock()
        agent.search = AsyncMock(
            return_value={
                **make_agent_state(),
                "sql_results": rows,
                "sql_keyword": keyword,
            }
        )
        return agent

    async def test_sql_channel_kind(self):
        nodes = _make_nodes(sql_agent=self._make_sql_agent([]))
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        channel = result["search_channels"][SearchChannel.SQL]
        assert channel["kind"] == SearchKind.SQL

    async def test_sql_channel_query_text_from_keyword(self):
        nodes = _make_nodes(sql_agent=self._make_sql_agent([], keyword="헬스장"))
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        channel = result["search_channels"][SearchChannel.SQL]
        assert channel["query"]["query_text"] == "헬스장"

    async def test_sql_channel_query_text_none_when_no_keyword(self):
        nodes = _make_nodes(sql_agent=self._make_sql_agent([], keyword=None))
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        channel = result["search_channels"][SearchChannel.SQL]
        assert channel["query"]["query_text"] is None

    async def test_sql_channel_hits_from_results(self):
        rows = [
            {"service_id": "SVC001", "service_name": "수영장"},
            {"service_id": "SVC002", "service_name": "헬스장"},
        ]
        nodes = _make_nodes(sql_agent=self._make_sql_agent(rows))
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        hits = result["search_channels"][SearchChannel.SQL]["hits"]
        assert len(hits) == 2
        assert hits[0]["rank"] == 1
        assert hits[0]["service_id"] == "SVC001"
        assert hits[0]["score"] is None  # SQL 채널은 score 없음

    async def test_sql_channel_parameters_include_filters(self):
        nodes = _make_nodes(sql_agent=self._make_sql_agent([]))
        nodes.data_session = _mock_session()
        state = make_agent_state(
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
        )

        result = await nodes.sql_node(state)

        params = result["search_channels"][SearchChannel.SQL]["query"]["parameters"]
        assert params["max_class_name"] == "체육시설"
        assert params["area_name"] == "강남구"
        assert params["service_status"] == "접수중"

    async def test_sql_node_error_returns_no_search_channels(self):
        """예외 발생 시 search_channels 를 반환하지 않는다."""
        agent = MagicMock()
        agent.search = AsyncMock(side_effect=RuntimeError("DB 연결 오류"))
        nodes = _make_nodes(sql_agent=agent)
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        assert "error" in result
        assert "search_channels" not in result

    async def test_sql_node_results_none_gives_empty_hits(self):
        """sql_results=None 을 반환하면 hits=[] 로 처리된다."""
        agent = MagicMock()
        agent.search = AsyncMock(
            return_value={
                **make_agent_state(),
                "sql_results": None,
                "sql_keyword": None,
            }
        )
        nodes = _make_nodes(sql_agent=agent)
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.sql_node(state)

        hits = result["search_channels"][SearchChannel.SQL]["hits"]
        assert hits == []


# ---------------------------------------------------------------------------
# vector_node ChannelData
# ---------------------------------------------------------------------------


class TestVectorNodeChannelData:
    def _make_vector_agent(
        self,
        hydrated: list[dict],
        refined_query: str = "서울 수영장",
        vector_rows: list[dict] | None = None,
        bm25_rows: list[dict] | None = None,
        bm25_tokens: list[str] | None = None,
    ) -> MagicMock:
        """VectorAgent.search() 를 mock 으로 대체한다."""
        from schemas.search import ChannelData, ChannelQuery, SearchChannel, SearchKind
        from agents._search_channel_utils import _to_hits

        vrows = vector_rows or []
        brows = bm25_rows or []
        btokens = bm25_tokens or []

        channels = {
            SearchChannel.VECTOR: ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(query_text=refined_query, parameters={"top_k": 10}),
                hits=_to_hits(vrows, score_field="similarity"),
            ),
            SearchChannel.BM25: ChannelData(
                kind=SearchKind.BM25,
                query=ChannelQuery(
                    query_text=" ".join(btokens) if btokens else None,
                    parameters={"tokens": btokens, "top_k": 10},
                ),
                hits=_to_hits(brows, score_field="bm25_score"),
            ),
            SearchChannel.FINAL: ChannelData(
                kind=SearchKind.FINAL,
                query=ChannelQuery(
                    query_text=None,
                    parameters={"source_channels": ["vector", "bm25"], "hydration_applied": True},
                ),
                hits=_to_hits(hydrated, score_field="rrf_score"),
            ),
        }
        agent = MagicMock()
        agent.search = AsyncMock(
            return_value={
                **make_agent_state(),
                "refined_query": refined_query,
                "vector_results": hydrated,
                "search_channels": channels,
            }
        )
        return agent

    async def test_vector_bm25_final_channels_present(self):
        nodes = _make_nodes(vector_agent=self._make_vector_agent([]))
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        channels = result["search_channels"]
        assert SearchChannel.VECTOR in channels
        assert SearchChannel.BM25 in channels
        assert SearchChannel.FINAL in channels

    async def test_vector_channel_kind(self):
        nodes = _make_nodes(vector_agent=self._make_vector_agent([]))
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        assert result["search_channels"][SearchChannel.VECTOR]["kind"] == SearchKind.VECTOR
        assert result["search_channels"][SearchChannel.BM25]["kind"] == SearchKind.BM25
        assert result["search_channels"][SearchChannel.FINAL]["kind"] == SearchKind.FINAL

    async def test_final_channel_query_text_is_none(self):
        nodes = _make_nodes(vector_agent=self._make_vector_agent([]))
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        final = result["search_channels"][SearchChannel.FINAL]
        assert final["query"]["query_text"] is None

    async def test_vector_channel_hits_from_vector_rows(self):
        vrows = [{"service_id": "SVC001", "similarity": 0.92}]
        agent = self._make_vector_agent([], vector_rows=vrows)
        # override VECTOR channel hits directly
        agent.search.return_value["search_channels"][SearchChannel.VECTOR]["hits"] = [
            ChannelHit(rank=1, service_id="SVC001", score=0.92, meta={})
        ]
        nodes = _make_nodes(vector_agent=agent)
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        vector_hits = result["search_channels"][SearchChannel.VECTOR]["hits"]
        assert len(vector_hits) == 1
        assert vector_hits[0]["service_id"] == "SVC001"
        assert vector_hits[0]["score"] == pytest.approx(0.92)

    async def test_vector_node_no_channels_if_agent_returns_empty(self):
        """VectorAgent 가 search_channels={} 를 반환하면 reducer 리셋 방지용으로 전파하지 않는다."""
        agent = MagicMock()
        agent.search = AsyncMock(
            return_value={
                **make_agent_state(),
                "refined_query": "test",
                "vector_results": [],
                "search_channels": {},
            }
        )
        nodes = _make_nodes(vector_agent=agent)
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        # 빈 dict 는 전파하지 않아야 한다 (reducer 리셋 방지)
        assert "search_channels" not in result

    async def test_vector_node_error_returns_no_search_channels(self):
        agent = MagicMock()
        agent.search = AsyncMock(side_effect=RuntimeError("임베딩 오류"))
        nodes = _make_nodes(vector_agent=agent)
        nodes.ai_session = _mock_session()
        nodes.data_session = _mock_session()
        state = make_agent_state()

        result = await nodes.vector_node(state)

        assert "error" in result
        assert "search_channels" not in result


# ---------------------------------------------------------------------------
# map_node ChannelData
# ---------------------------------------------------------------------------


class TestMapNodeChannelData:
    def _make_geojson(self, features: list[dict]) -> dict:
        return {"type": "FeatureCollection", "features": features}

    def _make_feature(
        self, service_id: str, distance_m: int = 500
    ) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [126.97, 37.56]},
            "properties": {
                "service_id": service_id,
                "service_name": f"시설_{service_id}",
                "distance_m": distance_m,
            },
        }

    async def test_map_channel_present(self):
        geojson = self._make_geojson([self._make_feature("SVC001", 300)])
        nodes = _make_nodes()
        nodes.data_session = _mock_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            state = make_agent_state(lat=37.56, lng=126.97)
            result = await nodes.map_node(state)

        assert SearchChannel.MAP in result["search_channels"]

    async def test_map_channel_kind(self):
        geojson = self._make_geojson([self._make_feature("SVC001")])
        nodes = _make_nodes()
        nodes.data_session = _mock_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            state = make_agent_state(lat=37.56, lng=126.97)
            result = await nodes.map_node(state)

        channel = result["search_channels"][SearchChannel.MAP]
        assert channel["kind"] == SearchKind.MAP

    async def test_map_channel_query_text_format(self):
        geojson = self._make_geojson([])
        nodes = _make_nodes()
        nodes.data_session = _mock_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            state = make_agent_state(lat=37.5665, lng=126.9780)
            result = await nodes.map_node(state)

        query_text = result["search_channels"][SearchChannel.MAP]["query"]["query_text"]
        assert "lat=37.5665" in query_text
        assert "lng=126.978" in query_text
        assert "r=" in query_text

    async def test_map_channel_hits_from_features(self):
        features = [
            self._make_feature("SVC001", 300),
            self._make_feature("SVC002", 600),
        ]
        geojson = self._make_geojson(features)
        nodes = _make_nodes()
        nodes.data_session = _mock_session()

        with patch("agents.nodes.map_search", AsyncMock(return_value=geojson)):
            state = make_agent_state(lat=37.56, lng=126.97)
            result = await nodes.map_node(state)

        hits = result["search_channels"][SearchChannel.MAP]["hits"]
        assert len(hits) == 2
        assert hits[0]["rank"] == 1
        assert hits[0]["service_id"] == "SVC001"
        assert hits[0]["score"] == pytest.approx(300.0)
        assert hits[0]["meta"]["distance_m"] == 300

    async def test_map_no_channel_when_lat_lng_missing(self):
        """lat/lng 없으면 map_results=None + search_channels 미포함."""
        nodes = _make_nodes()
        nodes.data_session = _mock_session()
        state = make_agent_state(lat=None, lng=None)

        result = await nodes.map_node(state)

        assert result["map_results"] is None
        assert "search_channels" not in result

    async def test_map_node_error_returns_no_search_channels(self):
        nodes = _make_nodes()
        nodes.data_session = _mock_session()

        with patch("agents.nodes.map_search", AsyncMock(side_effect=RuntimeError("DB 오류"))):
            state = make_agent_state(lat=37.56, lng=126.97)
            result = await nodes.map_node(state)

        assert "error" in result
        assert "search_channels" not in result
