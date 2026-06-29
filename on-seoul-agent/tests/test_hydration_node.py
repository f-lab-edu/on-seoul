"""HydrationNode 단위 테스트.

동작:
- VECTOR_SEARCH: service_id 추출 → hydrate_services 호출 → 메타 머지 → hydrated_services
- SQL_SEARCH:    sql_results 통과 (sql_search 가 이미 원본 반환)
- 그 외 intent:  빈 리스트
- 재호출 안전 (cache hit envelope 복원 후 등)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.hydration_node import (
    HydrationNode,
    _extract_service_ids,
    _filter_by_payment,
    _merge_search_meta,
)
from schemas.state import IntentType
from tests.helpers import make_agent_state as _st


# ---------------------------------------------------------------------------
# _extract_service_ids
# ---------------------------------------------------------------------------


class TestExtractServiceIds:
    def test_vector_search_extracts_from_vector_results(self):
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[
                {"service_id": "S001", "rrf_score": 0.5},
                {"service_id": "S002", "rrf_score": 0.3},
            ],
            sql_results=[{"service_id": "SQL001"}],  # 무시되어야 함
        )
        assert _extract_service_ids(state) == ["S001", "S002"]

    def test_sql_search_extracts_from_sql_results(self):
        state = _st(
            intent=IntentType.SQL_SEARCH,
            sql_results=[{"service_id": "S100"}, {"service_id": "S200"}],
            vector_results=[{"service_id": "VEC001"}],  # 무시되어야 함
        )
        assert _extract_service_ids(state) == ["S100", "S200"]

    def test_other_intent_returns_empty(self):
        for intent in (IntentType.MAP, IntentType.FALLBACK, None):
            state = _st(
                intent=intent,
                vector_results=[{"service_id": "X"}],
                sql_results=[{"service_id": "Y"}],
            )
            assert _extract_service_ids(state) == []

    def test_skip_rows_without_service_id(self):
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[
                {"service_id": "S001"},
                {"rrf_score": 0.5},  # service_id 없음 → 스킵
                {"service_id": "S002"},
            ],
        )
        assert _extract_service_ids(state) == ["S001", "S002"]

    def test_empty_results_returns_empty(self):
        state = _st(intent=IntentType.VECTOR_SEARCH, vector_results=[])
        assert _extract_service_ids(state) == []

    def test_preserves_search_rank_order(self):
        """검색 랭킹 순서가 그대로 유지된다 (RRF score 내림차순)."""
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[
                {"service_id": "rank1", "rrf_score": 0.9},
                {"service_id": "rank2", "rrf_score": 0.5},
                {"service_id": "rank3", "rrf_score": 0.1},
            ],
        )
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
# HydrationNode.__call__
# ---------------------------------------------------------------------------


_PAY_ROWS = [
    {"service_id": "F1", "payment_type": "무료"},
    {"service_id": "P1", "payment_type": "유료"},
    {"service_id": "P2", "payment_type": "유료(요금안내문의)"},
]


class TestFilterByPayment:
    def test_none_passes_through(self):
        assert _filter_by_payment(_PAY_ROWS, None) == _PAY_ROWS

    def test_free_exact_only(self):
        out = _filter_by_payment(_PAY_ROWS, "무료")
        assert [r["service_id"] for r in out] == ["F1"]

    def test_paid_prefix_includes_variants(self):
        """유료=접두 — '유료'·'유료(요금안내문의)' 포함, '무료' 제외."""
        out = _filter_by_payment(_PAY_ROWS, "유료")
        assert [r["service_id"] for r in out] == ["P1", "P2"]

    def test_missing_payment_column_excluded_on_filter(self):
        rows = [{"service_id": "X"}]  # payment_type 키 없음
        assert _filter_by_payment(rows, "무료") == []


class TestHydrationVectorPaymentFilter:
    async def test_vector_path_applies_payment_filter(self):
        """VECTOR 경로 hydration 직후 payment_type='무료' post-filter 적용."""
        vector_results = [{"service_id": "F1"}, {"service_id": "P1"}]
        hydrated_rows = [
            {"service_id": "F1", "payment_type": "무료"},
            {"service_id": "P1", "payment_type": "유료"},
        ]
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=vector_results,
            payment_type="무료",
        )
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(return_value=hydrated_rows),
        ):
            update = await node(state, MagicMock())
        ids = [r["service_id"] for r in update["hydration"]["hydrated_services"]]
        assert ids == ["F1"]


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
        state = _st(intent=IntentType.SQL_SEARCH, sql_results=sql_results)
        node = HydrationNode()
        update = await node(state, data_session)

        assert update["hydration"]["hydrated_services"] == sql_results
        # 원본 list 와 다른 새 list (얕은 복사) — 후속 수정이 sql_results 에 영향 X
        assert update["hydration"]["hydrated_services"] is not sql_results

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
        state = _st(intent=IntentType.VECTOR_SEARCH, vector_results=vector_results)
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(return_value=hydrated_rows),
        ) as mock_hydrate:
            update = await node(state, data_session)

        mock_hydrate.assert_awaited_once_with(data_session, ["S1", "S2"])
        # 원본 필드 + 검색 메타 머지 확인
        result = update["hydration"]["hydrated_services"]
        assert len(result) == 2
        assert result[0]["service_name"] == "시설1"
        assert result[0]["rrf_score"] == 0.9  # 메타 머지됨
        assert result[1]["service_name"] == "시설2"
        assert result[1]["rrf_score"] == 0.5

    async def test_other_intent_returns_empty(self, data_session):
        """MAP / FALLBACK 등 — hydration 대상 아님."""
        for intent in (IntentType.MAP, IntentType.FALLBACK):
            state = _st(intent=intent)
            node = HydrationNode()
            update = await node(state, data_session)
            assert update == {"hydration": {"hydrated_services": []}}

    async def test_idempotent_when_already_hydrated(self, data_session):
        """이미 hydrated_services 가 채워져 있으면(cache hit 등) 재호출하지 않는다."""
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[{"service_id": "S1", "rrf_score": 0.5}],
            hydrated_services=[
                {"service_id": "S_CACHED", "service_name": "캐시된시설"}
            ],
        )
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
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[{"service_id": "S1", "rrf_score": 0.5}],
            hydrated_services=[],  # MAP/FALLBACK 경로나 이전 hydration 실패 결과
        )
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
        state = _st(intent=IntentType.VECTOR_SEARCH, vector_results=[])
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services", new=AsyncMock()
        ) as mock_hydrate:
            update = await node(state, data_session)
        mock_hydrate.assert_not_awaited()
        assert update["hydration"]["hydrated_services"] == []

    async def test_vector_hydrate_failure_returns_empty(self, data_session):
        """hydrate_services 예외 시 hydrated_services = [] (오류 전파 X)."""
        state = _st(
            intent=IntentType.VECTOR_SEARCH,
            vector_results=[{"service_id": "S1", "rrf_score": 0.9}],
        )
        node = HydrationNode()
        with patch(
            "agents.hydration_node.hydrate_services",
            new=AsyncMock(side_effect=RuntimeError("DB 오류")),
        ):
            update = await node(state, data_session)
        assert update["hydration"]["hydrated_services"] == []


# ---------------------------------------------------------------------------
# HydrationNode.hydrate_by_service_ids (핵심 API — 미래 사용 + 단위 검증)
# ---------------------------------------------------------------------------