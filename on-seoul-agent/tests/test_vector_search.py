"""tools/vector_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB 및 OpenAI/Gemini API에 접근하지 않는다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.vector_search import MIN_SIMILARITY, SCAN_K_MULTIPLIER, TOP_K, vector_search


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = ["service_id", "service_name", "metadata", "similarity"]
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _capture_session() -> tuple[MagicMock, list[str]]:
    """SQL 텍스트를 캡처하는 세션과 텍스트 저장소를 반환한다."""
    executed_sql_texts: list[str] = []

    async def _capture_execute(stmt, params=None):
        executed_sql_texts.append(str(stmt))
        mock_result = MagicMock()
        mock_result.keys.return_value = ["service_id", "service_name", "metadata", "similarity"]
        mock_result.fetchall.return_value = []
        return mock_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_capture_execute)
    return session, executed_sql_texts


_SAMPLE_ROWS = [
    {
        "service_id": "S001",
        "service_name": "어린이 체험관",
        "metadata": {"area_name": "강남구"},
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


class TestPostFilterStructure:
    """post-filter SQL 구조 검증: 서브쿼리 분리, scan_k 바인딩, 외부 필터 적용."""

    async def test_scan_k_bind_present(self):
        """scan_k가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR)
        bind = session.execute.call_args[0][1]
        assert "scan_k" in bind

    async def test_scan_k_default_value(self):
        """scan_k 기본값은 top_k × SCAN_K_MULTIPLIER 이다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR)
        bind = session.execute.call_args[0][1]
        assert bind["scan_k"] == TOP_K * SCAN_K_MULTIPLIER

    async def test_scan_k_scales_with_top_k(self):
        """top_k=5 전달 시 scan_k = 5 × SCAN_K_MULTIPLIER 이다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, top_k=5)
        bind = session.execute.call_args[0][1]
        assert bind["scan_k"] == 5 * SCAN_K_MULTIPLIER

    async def test_sql_contains_subquery_candidates(self):
        """SQL에 서브쿼리 별칭 candidates가 포함된다 (서브쿼리 구조 검증)."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "candidates" in texts[0]

    async def test_sql_contains_scan_k_limit(self):
        """SQL 서브쿼리에 scan_k LIMIT이 포함된다."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        assert "scan_k" in texts[0]

    async def test_no_filter_no_post_filter_clause(self):
        """필터 없을 때 외부 WHERE 절의 metadata 조건이 SQL에 없다."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        # 필터 없을 때 post-filter 조건 컬럼명이 SQL에 없어야 한다
        assert "max_class_name" not in sql
        assert "area_name" not in sql
        assert "service_status" not in sql

    async def test_filter_applied_outside_subquery(self):
        """area_name 필터가 서브쿼리 외부(candidates 이후)에 위치한다."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR, area_name="강남구")
        sql = texts[0]
        # candidates 별칭 이후에 area_name 조건이 등장해야 한다
        candidates_pos = sql.find("candidates")
        area_name_pos = sql.find("area_name")
        assert candidates_pos != -1
        assert area_name_pos != -1
        assert area_name_pos > candidates_pos


class TestPostFilterBindParams:
    """post-filter 파라미터가 bind에 올바르게 전달된다."""

    async def test_postfilter_max_class_name_in_bind(self):
        """max_class_name 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, max_class_name="체육")
        bind = session.execute.call_args[0][1]
        assert "max_class_name" in bind
        assert bind["max_class_name"] == "체육"

    async def test_postfilter_area_name_in_bind(self):
        """area_name 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, area_name="강남구")
        bind = session.execute.call_args[0][1]
        assert "area_name" in bind
        assert bind["area_name"] == "강남구"

    async def test_postfilter_service_status_in_bind(self):
        """service_status 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, service_status="접수중")
        bind = session.execute.call_args[0][1]
        assert "service_status" in bind
        assert bind["service_status"] == "접수중"

    async def test_no_filter_excludes_postfilter_keys(self):
        """필터를 전달하지 않으면 bind에 post-filter 키가 없다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR)
        bind = session.execute.call_args[0][1]
        assert "max_class_name" not in bind
        assert "area_name" not in bind
        assert "service_status" not in bind

    async def test_all_three_postfilters_present_in_bind(self):
        """세 post-filter를 동시에 전달하면 bind에 세 키가 모두 포함된다."""
        session = _make_session([])
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            max_class_name="체육",
            area_name="강남구",
            service_status="접수중",
        )
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "체육"
        assert bind["area_name"] == "강남구"
        assert bind["service_status"] == "접수중"

    async def test_all_three_postfilters_appear_in_sql_text(self):
        """세 post-filter를 동시에 전달하면 SQL 문자열에 세 조건 절이 모두 포함된다."""
        session, texts = _capture_session()
        await vector_search(
            session,
            _SAMPLE_VECTOR,
            max_class_name="체육",
            area_name="강남구",
            service_status="접수중",
        )
        assert len(texts) == 1
        sql_text = texts[0]
        assert "max_class_name" in sql_text, "max_class_name 조건이 SQL에 없음"
        assert "area_name" in sql_text, "area_name 조건이 SQL에 없음"
        assert "service_status" in sql_text, "service_status 조건이 SQL에 없음"

    async def test_filter_values_not_inlined_in_sql_text(self):
        """SQL Injection 방지: post-filter 값이 SQL 문자열에 직접 삽입되지 않는다."""
        injected_values = [
            "'; DROP TABLE service_embeddings; --",
            "' OR '1'='1",
            "<script>alert(1)</script>",
        ]

        for bad_value in injected_values:
            session, texts = _capture_session()
            await vector_search(
                session,
                _SAMPLE_VECTOR,
                max_class_name=bad_value,
                area_name=bad_value,
                service_status=bad_value,
            )
            sql_text = texts[0]
            assert bad_value not in sql_text, (
                f"SQL Injection 위험: 값 '{bad_value}'이 SQL 문자열에 직접 삽입됨"
            )


class TestMinSimilarityPosition:
    """min_similarity 조건이 서브쿼리 외부(candidates 이후)에 위치하는지 검증."""

    async def test_min_similarity_outside_subquery(self):
        """min_similarity 조건이 candidates 별칭 이후에 등장한다 (post-filter 위치)."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        candidates_pos = sql.find("candidates")
        min_similarity_pos = sql.find("min_similarity")
        assert candidates_pos != -1, "candidates 별칭이 SQL에 없음"
        assert min_similarity_pos != -1, "min_similarity가 SQL에 없음"
        assert min_similarity_pos > candidates_pos, (
            "min_similarity 조건이 서브쿼리 내부에 있음 (candidates 이전)"
        )

    async def test_subquery_has_no_min_similarity_filter(self):
        """서브쿼리 내부(candidates 이전)에 min_similarity 조건이 없다."""
        session, texts = _capture_session()
        await vector_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        candidates_pos = sql.find("candidates")
        subquery_part = sql[:candidates_pos]
        assert "min_similarity" not in subquery_part, (
            "서브쿼리 내부에 min_similarity 조건이 있어 HNSW scan_k 완충 효과가 무력화됨"
        )


class TestVectorSearchBindParams:
    async def test_query_vector_converted_to_str(self):
        """query_vector는 str()로 변환되어 bind에 전달된다."""
        vector = [0.1, 0.2, 0.3]
        session = _make_session([])
        await vector_search(session, vector)
        bind = session.execute.call_args[0][1]
        assert bind["query_vector"] == str(vector)

    async def test_min_similarity_bind_default(self):
        """min_similarity 기본값이 bind에 전달된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR)
        bind = session.execute.call_args[0][1]
        assert bind["min_similarity"] == MIN_SIMILARITY

    async def test_top_k_bind_default(self):
        """top_k 기본값이 bind에 전달된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == TOP_K

    async def test_custom_top_k_override(self):
        """top_k=5 전달 시 bind["top_k"] == 5 이다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, top_k=5)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == 5

    async def test_custom_min_similarity_override(self):
        """min_similarity=0.8 전달 시 bind에 반영된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, min_similarity=0.8)
        bind = session.execute.call_args[0][1]
        assert bind["min_similarity"] == 0.8

    async def test_min_similarity_zero_passes_all_rows(self):
        """min_similarity=0.0 전달 시 threshold bind 파라미터에 0.0이 정확히 설정된다."""
        session = _make_session([])
        await vector_search(session, _SAMPLE_VECTOR, min_similarity=0.0)
        bind = session.execute.call_args[0][1]
        assert bind["min_similarity"] == 0.0
