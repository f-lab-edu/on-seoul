"""tools/vector_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB 및 OpenAI/Gemini API에 접근하지 않는다.

Phase RRF: row_kind 파라미터 기반 트랙별 독립 쿼리 + post-filter 복구.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.config import settings
from tools.vector_search import vector_search

# Phase RRF 컬럼: service_id, embedding_text, metadata, similarity
_RRF_KEYS = ["service_id", "embedding_text", "metadata", "similarity"]


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = _RRF_KEYS
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _capture_session() -> tuple[MagicMock, list[str], list[dict]]:
    """SQL 텍스트와 bind 파라미터를 캡처하는 세션을 반환한다."""
    executed_sql_texts: list[str] = []
    executed_bind_params: list[dict] = []

    async def _capture_execute(stmt, params=None):
        executed_sql_texts.append(str(stmt))
        executed_bind_params.append(params or {})
        mock_result = MagicMock()
        mock_result.keys.return_value = _RRF_KEYS
        mock_result.fetchall.return_value = []
        return mock_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_capture_execute)
    return session, executed_sql_texts, executed_bind_params


_SAMPLE_ROWS = [
    {
        "service_id": "S001",
        "embedding_text": "강남구 체육시설 헬스장",
        "metadata": {"max_class_name": "체육시설", "area_name": "강남구"},
        "similarity": 0.85,
    }
]
_SAMPLE_VECTOR = [0.1, 0.2, 0.3]


class TestVectorSearchBasic:
    async def test_returns_list_of_dicts(self):
        """기본 검색 결과가 리스트로 반환된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await vector_search(session, _SAMPLE_VECTOR)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["service_id"] == "S001"

    async def test_empty_result_returns_empty_list(self):
        """결과가 없을 때 None이 아닌 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await vector_search(session, _SAMPLE_VECTOR)
        assert result == []
        assert result is not None

    async def test_result_has_embedding_text_field(self):
        """결과 dict에 embedding_text 필드가 포함된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await vector_search(session, _SAMPLE_VECTOR)
        assert "embedding_text" in result[0]

    async def test_result_has_metadata_field(self):
        """결과 dict에 metadata 필드가 포함된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await vector_search(session, _SAMPLE_VECTOR)
        assert "metadata" in result[0]

    async def test_result_has_similarity_field(self):
        """결과 dict에 similarity 필드가 포함된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await vector_search(session, _SAMPLE_VECTOR)
        assert "similarity" in result[0]


class TestVectorSearchRowKind:
    """row_kind 파라미터 검증."""

    async def test_identity_filters_row_kind(self):
        """row_kind='identity' 전달 시 SQL에 row_kind = :row_kind 바인딩이 포함된다."""
        session, texts, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, row_kind="identity")
        assert "row_kind" in texts[0]
        assert binds[0]["row_kind"] == "identity"

    async def test_summary_filters_row_kind(self):
        """row_kind='summary' 전달 시 bind에 row_kind='summary'가 전달된다."""
        session, texts, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, row_kind="summary")
        assert binds[0]["row_kind"] == "summary"

    async def test_invalid_row_kind_raises(self):
        """허용되지 않는 row_kind 전달 시 ValueError가 발생한다."""
        session = _make_session([])
        with pytest.raises(ValueError, match="invalid row_kind"):
            await vector_search(session, _SAMPLE_VECTOR, row_kind="question")  # type: ignore[arg-type]

    async def test_identity_applies_post_filter(self):
        """row_kind='identity' + max_class_name 전달 시 bind에 max_class_name이 포함된다."""
        session, texts, binds = _capture_session()
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            row_kind="identity",
            max_class_name="체육시설",
        )
        assert "max_class_name" in texts[0]
        assert binds[0]["max_class_name"] == "체육시설"

    async def test_summary_without_filter_omits_postfilter_from_sql(self):
        """row_kind='summary' + 필터 없으면 post-filter WHERE 절이 SQL에 없다.

        None 파라미터를 bind하면 asyncpg가 AmbiguousParameterError를 발생시키므로
        필터 없을 때는 조건 절 자체를 생성하지 않는다.
        """
        session, texts, binds = _capture_session()
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            row_kind="summary",
        )
        # None인 필터는 SQL/bind 모두 제외
        assert "max_class_name" not in texts[0]
        assert "max_class_name" not in binds[0]
        assert "area_name" not in binds[0]
        assert "service_status" not in binds[0]


class TestVectorSearchQueryStructure:
    """SQL 구조 검증."""

    async def test_sql_contains_subquery_candidates(self):
        """SQL에 서브쿼리 별칭 candidates가 포함된다."""
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "candidates" in texts[0]

    async def test_sql_contains_scan_k_limit(self):
        """SQL 서브쿼리에 scan_k LIMIT이 포함된다."""
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "scan_k" in texts[0]

    async def test_min_similarity_inside_subquery(self):
        """min_similarity 조건이 서브쿼리(candidates) 내부에 있다."""
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        candidates_pos = sql.find("candidates")
        min_similarity_pos = sql.find("min_similarity")
        assert candidates_pos != -1, "candidates 별칭이 SQL에 없음"
        assert min_similarity_pos != -1, "min_similarity가 SQL에 없음"
        assert min_similarity_pos < candidates_pos, (
            "min_similarity 조건이 서브쿼리 외부에 있음"
        )

    async def test_no_distinct_on(self):
        """Phase RRF에서는 DISTINCT ON이 없다 (트랙별 독립 쿼리이므로 불필요)."""
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "DISTINCT ON" not in texts[0]


class TestPostFilterStructure:
    """post-filter SQL 구조 검증."""

    async def test_filter_applied_outside_subquery(self):
        """area_name 필터가 서브쿼리(candidates) 외부에 적용된다."""
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, area_name="강남구")
        sql = texts[0]
        candidates_pos = sql.find("candidates")
        area_name_pos = sql.rfind("area_name")  # 외부 WHERE 기준 마지막 등장
        assert candidates_pos != -1
        assert area_name_pos != -1
        assert area_name_pos > candidates_pos

    async def test_postfilter_absent_when_no_filter_given(self):
        """필터가 없으면 post-filter WHERE 절이 SQL에 존재하지 않는다.

        None을 bind하면 asyncpg AmbiguousParameterError 발생 → 조건 자체를 생략.
        """
        session, texts, _ = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "max_class_name" not in sql
        assert "area_name" not in sql
        assert "service_status" not in sql


class TestPostFilterBindParams:
    """post-filter 파라미터 bind 검증."""

    async def test_postfilter_max_class_name_in_bind(self):
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, max_class_name="체육시설")
        assert "max_class_name" in binds[0]
        assert binds[0]["max_class_name"] == "체육시설"

    async def test_postfilter_area_name_in_bind(self):
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, area_name="강남구")
        assert "area_name" in binds[0]
        assert binds[0]["area_name"] == "강남구"

    async def test_postfilter_service_status_in_bind(self):
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, service_status="접수중")
        assert "service_status" in binds[0]
        assert binds[0]["service_status"] == "접수중"

    async def test_no_filter_excludes_postfilter_keys_from_bind(self):
        """필터 없을 때 post-filter 키가 bind에 포함되지 않는다.

        None을 bind에 포함하면 asyncpg AmbiguousParameterError 발생.
        """
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "max_class_name" not in binds[0]
        assert "area_name" not in binds[0]
        assert "service_status" not in binds[0]

    async def test_all_three_postfilters_present_in_bind(self):
        session, _, binds = _capture_session()
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
        )
        assert binds[0]["max_class_name"] == "체육시설"
        assert binds[0]["area_name"] == "강남구"
        assert binds[0]["service_status"] == "접수중"

    async def test_all_three_postfilters_appear_in_sql_text(self):
        session, texts, _ = _capture_session()
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
        )
        sql_text = texts[0]
        assert "max_class_name" in sql_text
        assert "area_name" in sql_text
        assert "service_status" in sql_text

    async def test_filter_values_not_inlined_in_sql_text(self):
        injected_values = [
            "'; DROP TABLE service_embeddings; --",
            "' OR '1'='1",
            "<script>alert(1)</script>",
        ]
        for bad_value in injected_values:
            session, texts, _ = _capture_session()
            await vector_search(
                session,
                _SAMPLE_VECTOR,
                max_class_name=bad_value,
                area_name=bad_value,
                service_status=bad_value,
            )
            sql_text = texts[0]
            assert bad_value not in sql_text


class TestVectorSearchBindParams:
    async def test_query_vector_converted_to_str(self):
        """query_vector는 str()로 변환되어 bind에 전달된다."""
        vector = [0.1, 0.2, 0.3]
        session, _, binds = _capture_session()
        await vector_search(session, vector)
        assert binds[0]["query_vector"] == str(vector)

    async def test_min_similarity_bind_default_identity(self):
        """기본 row_kind='identity'의 min_similarity 기본값은 identity 트랙 config 값."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert binds[0]["min_similarity"] == settings.vector_min_similarity_identity

    async def test_min_similarity_bind_default_summary(self):
        """row_kind='summary'의 min_similarity 기본값은 summary 트랙 config 값."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, row_kind="summary")
        assert binds[0]["min_similarity"] == settings.vector_min_similarity_summary

    async def test_top_k_bind_default(self):
        """top_k 기본값이 config(vector_track_top_k)로 bind에 전달된다."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert binds[0]["top_k"] == settings.vector_track_top_k

    async def test_custom_top_k_override(self):
        """top_k=5 전달 시 bind["top_k"] == 5 이다."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, top_k=5)
        assert binds[0]["top_k"] == 5

    async def test_custom_min_similarity_override(self):
        """min_similarity=0.8 전달 시 bind에 반영된다."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, min_similarity=0.8)
        assert binds[0]["min_similarity"] == 0.8

    async def test_min_similarity_zero_passes_all_rows(self):
        """min_similarity=0.0 전달 시 threshold bind 파라미터에 0.0이 정확히 설정된다."""
        session, _, binds = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, min_similarity=0.0)
        assert binds[0]["min_similarity"] == 0.0
