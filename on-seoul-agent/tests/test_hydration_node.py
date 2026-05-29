"""HydrationNode 단위 테스트.

Phase 2 동작:
- VECTOR_SEARCH: service_id 추출 → hydrate_services 호출 → 메타 머지 → hydrated_services
- SQL_SEARCH:    sql_results 통과 (sql_search 가 이미 원본 반환)
- 그 외 intent:  빈 리스트
- 재호출 안전 (cache hit envelope 복원 후 등)

별도 메서드(hydrate_by_service_ids):
- service_id 리스트로 직접 hydrate + 메타 머지 + FINAL 채널
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.hydration_node import (
    HydrationNode,
    _build_final_channel,
    _extract_service_ids,
    _merge_search_meta,
)
from schemas.search import SearchKind
from schemas.state import IntentType


# ---------------------------------------------------------------------------
# _extract_service_ids
# ---------------------------------------------------------------------------


class TestExtractServiceIds:
    def test_vector_search_extracts_from_vector_results(self):
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [
                {"service_id": "S001", "rrf_score": 0.5},
                {"service_id": "S002", "rrf_score": 0.3},
            ],
            "sql_results": [{"service_id": "SQL001"}],  # 무시되어야 함
        }
        assert _extract_service_ids(state) == ["S001", "S002"]

    def test_sql_search_extracts_from_sql_results(self):
        state = {
            "intent": IntentType.SQL_SEARCH,
            "sql_results": [{"service_id": "S100"}, {"service_id": "S200"}],
            "vector_results": [{"service_id": "VEC001"}],  # 무시되어야 함
        }
        assert _extract_service_ids(state) == ["S100", "S200"]

    def test_other_intent_returns_empty(self):
        for intent in (IntentType.MAP, IntentType.FALLBACK, None):
            state = {
                "intent": intent,
                "vector_results": [{"service_id": "X"}],
                "sql_results": [{"service_id": "Y"}],
            }
            assert _extract_service_ids(state) == []

    def test_skip_rows_without_service_id(self):
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [
                {"service_id": "S001"},
                {"rrf_score": 0.5},  # service_id 없음 → 스킵
                {"service_id": "S002"},
            ],
        }
        assert _extract_service_ids(state) == ["S001", "S002"]

    def test_empty_results_returns_empty(self):
        state = {"intent": IntentType.VECTOR_SEARCH, "vector_results": []}
        assert _extract_service_ids(state) == []

    def test_preserves_search_rank_order(self):
        """검색 랭킹 순서가 그대로 유지된다 (RRF score 내림차순)."""
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [
                {"service_id": "rank1", "rrf_score": 0.9},
                {"service_id": "rank2", "rrf_score": 0.5},
                {"service_id": "rank3", "rrf_score": 0.1},
            ],
        }
        assert _extract_service_ids(state) == ["rank1", "rank2", "rank3"]


# ---------------------------------------------------------------------------
# _merge_search_meta
# ---------------------------------------------------------------------------


class TestMergeSearchMeta:
    def test_merge_rrf_score_into_hydrated(self):
        hydrated = [
            {"service_id": "S1", "service_name": "테스트시설1"},
            {"service_id": "S2", "service_name": "테스트시설2"},
        ]
        source = [
            {"service_id": "S1", "rrf_score": 0.7},
            {"service_id": "S2", "rrf_score": 0.3},
        ]
        merged = _merge_search_meta(hydrated, source)
        assert merged[0]["rrf_score"] == 0.7
        assert merged[1]["rrf_score"] == 0.3
        assert merged[0]["service_name"] == "테스트시설1"  # 원본 보존

    def test_existing_key_in_hydrated_takes_precedence(self):
        """원본(hydrated)에 이미 있는 키는 검색 메타가 덮어쓰지 않는다."""
        hydrated = [{"service_id": "S1", "service_name": "원본명"}]
        source = [{"service_id": "S1", "service_name": "검색측이름"}]
        merged = _merge_search_meta(hydrated, source)
        assert merged[0]["service_name"] == "원본명"

    def test_missing_in_source_keeps_hydrated(self):
        """source 에 없는 service_id 의 hydrated 행은 그대로 둔다."""
        hydrated = [{"service_id": "S1"}, {"service_id": "S2"}]
        source = [{"service_id": "S1", "rrf_score": 0.5}]
        merged = _merge_search_meta(hydrated, source)
        assert merged[0]["rrf_score"] == 0.5
        assert "rrf_score" not in merged[1]


# ---------------------------------------------------------------------------
# _build_final_channel
# ---------------------------------------------------------------------------


class TestBuildFinalChannel:
    def test_kind_is_final(self):
        ch = _build_final_channel([])
        assert ch["kind"] == SearchKind.FINAL

    def test_hydration_applied_flag_in_parameters(self):
        ch = _build_final_channel([])
        assert ch["query"]["parameters"]["hydration_applied"] is True

    def test_hits_built_from_hydrated_rows_with_rrf_score(self):
        hydrated = [
            {"service_id": "S1", "rrf_score": 0.7},
            {"service_id": "S2", "rrf_score": 0.3},
        ]
        ch = _build_final_channel(hydrated)
        hits = ch["hits"]
        assert len(hits) == 2
        assert hits[0]["service_id"] == "S1"
        assert hits[0]["rank"] == 1
        assert hits[0]["score"] == 0.7


# ---------------------------------------------------------------------------
# HydrationNode.__call__ (Phase 1: 통과 모드)
# ---------------------------------------------------------------------------


class TestHydrationNodeCall:
    @pytest.fixture
    def data_session(self):
        return MagicMock()

    async def test_sql_path_passes_through_sql_results(self, data_session):
        """SQL_SEARCH 경로 — sql_results 를 그대로 hydrated_services 로 통과."""
        sql_results = [
            {"service_id": "S1", "service_name": "시설1", "service_url": "u1"},
            {"service_id": "S2", "service_name": "시설2", "service_url": "u2"},
        ]
        state = {"intent": IntentType.SQL_SEARCH, "sql_results": sql_results}
        node = HydrationNode()
        update = await node(state, data_session)

        assert update["hydrated_services"] == sql_results
        # 원본 list 와 다른 새 list (얕은 복사) — 후속 수정이 sql_results 에 영향 X
        assert update["hydrated_services"] is not sql_results

    async def test_vector_path_calls_hydrate_services(self, data_session):
        """VECTOR_SEARCH 경로 — service_id 추출 + hydrate_services 호출 + 메타 머지."""
        vector_results = [
            {"service_id": "S1", "rrf_score": 0.9},
            {"service_id": "S2", "rrf_score": 0.5},
        ]
        hydrated_rows = [
            {"service_id": "S1", "service_name": "시설1"},
            {"service_id": "S2", "service_name": "시설2"},
        ]
        state = {"intent": IntentType.VECTOR_SEARCH, "vector_results": vector_results}
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(return_value=hydrated_rows),
        ) as mock_hydrate:
            update = await node(state, data_session)

        mock_hydrate.assert_awaited_once_with(data_session, ["S1", "S2"])
        # 원본 필드 + 검색 메타 머지 확인
        result = update["hydrated_services"]
        assert len(result) == 2
        assert result[0]["service_name"] == "시설1"
        assert result[0]["rrf_score"] == 0.9  # 메타 머지됨
        assert result[1]["service_name"] == "시설2"
        assert result[1]["rrf_score"] == 0.5

    async def test_other_intent_returns_empty(self, data_session):
        """MAP / FALLBACK 등 — hydration 대상 아님."""
        for intent in (IntentType.MAP, IntentType.FALLBACK):
            state = {"intent": intent}
            node = HydrationNode()
            update = await node(state, data_session)
            assert update == {"hydrated_services": []}

    async def test_idempotent_when_already_hydrated(self, data_session):
        """이미 hydrated_services 가 채워져 있으면(cache hit 등) 재호출하지 않는다."""
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [{"service_id": "S1", "rrf_score": 0.5}],
            "hydrated_services": [
                {"service_id": "S_CACHED", "service_name": "캐시된시설"}
            ],
        }
        node = HydrationNode()
        update = await node(state, data_session)
        # 빈 dict 반환 → 기존 hydrated_services 그대로 유지
        assert update == {}

    async def test_idempotent_when_hydrated_services_is_empty_list(self, data_session):
        """hydrated_services=[] 빈 리스트도 '이미 처리됨' 으로 간주해 재실행하지 않는다.

        [] 는 MAP/FALLBACK 경로나 hydration 실패 후 설정되는 값이다.
        truthy 검사(`if state.get(...)`)는 [] 를 falsy 로 보아 재실행하는 버그가 있었다.
        is not None 검사로 [] 포함 비-None 상태를 일관되게 skip 한다.
        """
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [{"service_id": "S1", "rrf_score": 0.5}],
            "hydrated_services": [],  # MAP/FALLBACK 경로나 이전 hydration 실패 결과
        }
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services", new=AsyncMock()
        ) as mock_hydrate:
            update = await node(state, data_session)
        # hydrate_services 미호출 + 빈 dict 반환
        mock_hydrate.assert_not_awaited()
        assert update == {}

    async def test_empty_results_returns_empty_list(self, data_session):
        """검색 결과가 비어 있으면 hydrated_services = [] (hydrate_services 호출 없음)."""
        state = {"intent": IntentType.VECTOR_SEARCH, "vector_results": []}
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services", new=AsyncMock()
        ) as mock_hydrate:
            update = await node(state, data_session)
        mock_hydrate.assert_not_awaited()
        assert update["hydrated_services"] == []

    async def test_vector_hydrate_failure_returns_empty(self, data_session):
        """hydrate_services 예외 시 hydrated_services = [] (오류 전파 X)."""
        state = {
            "intent": IntentType.VECTOR_SEARCH,
            "vector_results": [{"service_id": "S1", "rrf_score": 0.9}],
        }
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(side_effect=RuntimeError("DB 오류")),
        ):
            update = await node(state, data_session)
        assert update["hydrated_services"] == []


# ---------------------------------------------------------------------------
# HydrationNode.hydrate_by_service_ids (Phase 2 의 핵심 API — 미래 사용 + 단위 검증)
# ---------------------------------------------------------------------------


class TestHydrateByServiceIds:
    @pytest.fixture
    def data_session(self):
        return MagicMock()

    async def test_calls_hydrate_with_given_ids(self, data_session):
        """service_id 리스트가 그대로 hydrate_services 에 전달된다."""
        hydrated_rows = [
            {"service_id": "S1", "service_name": "원본명1"},
            {"service_id": "S2", "service_name": "원본명2"},
        ]
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(return_value=hydrated_rows),
        ) as mock_hydrate:
            node = HydrationNode()
            hydrated, final = await node.hydrate_by_service_ids(
                ["S1", "S2"],
                [
                    {"service_id": "S1", "rrf_score": 0.9},
                    {"service_id": "S2", "rrf_score": 0.5},
                ],
                data_session,
            )

        mock_hydrate.assert_awaited_once_with(data_session, ["S1", "S2"])
        assert hydrated[0]["rrf_score"] == 0.9  # 메타 머지됨
        assert hydrated[0]["service_name"] == "원본명1"  # 원본 보존
        assert final is not None
        assert final["kind"] == SearchKind.FINAL

    async def test_empty_ids_returns_empty(self, data_session):
        """빈 service_id 리스트 → hydrate 호출 없이 ([], None)."""
        with patch(
            "agents.hydration_node.hydrate_services", new=AsyncMock()
        ) as mock_hydrate:
            node = HydrationNode()
            hydrated, final = await node.hydrate_by_service_ids([], [], data_session)
        mock_hydrate.assert_not_awaited()
        assert hydrated == []
        assert final is None

    async def test_hydrate_failure_returns_empty(self, data_session):
        """hydrate_services 예외 시 ([], None) fallback (오류 전파 X)."""
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(side_effect=RuntimeError("DB 오류")),
        ):
            node = HydrationNode()
            hydrated, final = await node.hydrate_by_service_ids(
                ["S1"], [], data_session
            )
        assert hydrated == []
        assert final is None

    async def test_preserves_rank_order(self, data_session):
        """hydrate_services 가 service_id 순서를 보존한다는 전제 하에 검색 랭킹 유지."""
        hydrated_in_order = [
            {"service_id": "rank1", "service_name": "1st"},
            {"service_id": "rank2", "service_name": "2nd"},
            {"service_id": "rank3", "service_name": "3rd"},
        ]
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(return_value=hydrated_in_order),
        ):
            node = HydrationNode()
            hydrated, _ = await node.hydrate_by_service_ids(
                ["rank1", "rank2", "rank3"], [], data_session
            )
        sids = [r["service_id"] for r in hydrated]
        assert sids == ["rank1", "rank2", "rank3"]
