"""tools/bm25_search.py 단위 테스트.

Mock DB 세션으로 BM25 쿼리 변환, bind 파라미터, 머지 동작을 검증한다.
실제 DB 및 ParadeDB 없이 동작한다.

테스트 구조:
- build_bm25_query: 토큰 sanitize/OR 결합 단위 함수
- bm25_search: 두 컬럼(service_name, metadata) 개별 호출 후 머지 동작
"""

from unittest.mock import AsyncMock, MagicMock

from tools.bm25_search import BM25_LIMIT, bm25_search, build_bm25_query


def _make_session(*responses: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출마다 responses 순서대로 row 리스트를 반환한다.

    bm25_search 가 두 컬럼을 순차 호출하므로 호출 횟수만큼 응답을 미리 준비한다.
    응답이 1개만 주어지면 모든 호출에 동일하게 반환된다.
    """
    def _result_for(rows: list[dict]) -> MagicMock:
        mr = MagicMock()
        if rows:
            mr.keys.return_value = list(rows[0].keys())
            mr.fetchall.return_value = [tuple(r.values()) for r in rows]
        else:
            mr.keys.return_value = ["service_id", "service_name", "bm25_score"]
            mr.fetchall.return_value = []
        return mr

    mock_results = [_result_for(r) for r in responses]

    session = MagicMock()
    if len(mock_results) == 1:
        session.execute = AsyncMock(return_value=mock_results[0])
    else:
        session.execute = AsyncMock(side_effect=mock_results)
    return session


_SAMPLE_ROWS_SN = [
    {"service_id": "S001", "service_name": "테니스장1", "bm25_score": 2.5},
    {"service_id": "S002", "service_name": "테니스장2", "bm25_score": 1.8},
]
_SAMPLE_ROWS_MD = [
    {"service_id": "S002", "service_name": "테니스장2", "bm25_score": 3.0},
    {"service_id": "S003", "service_name": "수영장1", "bm25_score": 1.2},
]


# ---------------------------------------------------------------------------
# build_bm25_query — sanitize + OR 결합
# ---------------------------------------------------------------------------


class TestBuildBm25Query:
    def test_single_token(self):
        assert build_bm25_query(["따릉이"]) == "따릉이"

    def test_multiple_tokens_joined_with_or(self):
        assert build_bm25_query(["따릉이", "대여소"]) == "따릉이 OR 대여소"

    def test_empty_tokens_returns_empty_string(self):
        assert build_bm25_query([]) == ""

    def test_special_chars_removed(self):
        """Tantivy 특수문자가 토큰에서 제거된다."""
        assert build_bm25_query(["체육*관"]) == "체육관"
        assert build_bm25_query(["수영~장"]) == "수영장"
        assert build_bm25_query(['"따릉이"']) == "따릉이"
        assert build_bm25_query(["(공원)"]) == "공원"
        assert build_bm25_query(["[강당]"]) == "강당"
        assert build_bm25_query(["{센터}"]) == "센터"
        assert build_bm25_query(["접수^중"]) == "접수중"

    def test_reserved_words_filtered_uppercase(self):
        for w in ["AND", "OR", "NOT", "TO", "IN"]:
            assert build_bm25_query([w]) == ""

    def test_reserved_words_filtered_lowercase(self):
        for w in ["and", "or", "not", "to", "in"]:
            assert build_bm25_query([w]) == ""

    def test_reserved_words_mixed_case_filtered(self):
        for w in ["And", "Or", "Not"]:
            assert build_bm25_query([w]) == ""

    def test_reserved_words_removed_from_token_list(self):
        assert build_bm25_query(["수영", "AND", "강습"]) == "수영 OR 강습"

    def test_special_chars_and_reserved_word_combined(self):
        """특수문자 제거 후 예약어가 되는 토큰도 필터링된다."""
        assert build_bm25_query(["AND*"]) == ""
        assert build_bm25_query(["(OR)"]) == ""

    def test_token_becomes_empty_after_special_char_removal(self):
        assert build_bm25_query(["***"]) == ""
        assert build_bm25_query(["***", "수영"]) == "수영"

    def test_plus_minus_removed(self):
        assert build_bm25_query(["+수영"]) == "수영"
        assert build_bm25_query(["-수영"]) == "수영"

    def test_colon_removed(self):
        assert build_bm25_query(["service:name"]) == "servicename"

    def test_backslash_removed(self):
        assert build_bm25_query(["수영\\장"]) == "수영장"

    def test_question_mark_removed(self):
        assert build_bm25_query(["수영?장"]) == "수영장"


# ---------------------------------------------------------------------------
# bm25_search — Empty query guard
# ---------------------------------------------------------------------------


class TestBm25SearchEmptyQueryGuard:
    async def test_empty_tokens_returns_empty_list_without_db(self):
        session = _make_session([])
        result = await bm25_search([], session)
        assert result == []
        session.execute.assert_not_called()

    async def test_all_reserved_tokens_returns_empty_list_without_db(self):
        session = _make_session([])
        result = await bm25_search(["AND", "OR", "NOT"], session)
        assert result == []
        session.execute.assert_not_called()

    async def test_all_special_char_tokens_returns_empty_list_without_db(self):
        session = _make_session([])
        result = await bm25_search(["***", "++", "---"], session)
        assert result == []
        session.execute.assert_not_called()


# ---------------------------------------------------------------------------
# bm25_search — Basic behavior
# ---------------------------------------------------------------------------


class TestBm25SearchBasic:
    async def test_single_token_executes_two_queries(self):
        """단일 토큰 × 2 컬럼 = 2회 execute."""
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        await bm25_search(["테니스"], session)
        assert session.execute.call_count == 2

    async def test_two_tokens_execute_four_queries(self):
        """2 토큰 × 2 컬럼 = 4회 execute."""
        session = _make_session([], [], [], [])
        await bm25_search(["테니스", "예약"], session)
        assert session.execute.call_count == 4

    async def test_returns_list_of_dicts(self):
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)

    async def test_result_has_required_keys(self):
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session)
        for row in result:
            assert "service_id" in row
            assert "service_name" in row
            assert "bm25_score" in row

    async def test_empty_results_in_all_queries(self):
        session = _make_session([], [])
        result = await bm25_search(["없는키워드"], session)
        assert result == []


# ---------------------------------------------------------------------------
# bm25_search — Merge by MAX(bm25_score)
# ---------------------------------------------------------------------------


class TestBm25SearchMerge:
    async def test_duplicate_service_id_takes_max_score(self):
        """두 컬럼 모두에 매칭된 service_id 는 최대 점수가 채택된다."""
        # S002 는 SN=1.8, MD=3.0 → MD 점수 채택
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session)
        s002 = next(r for r in result if r["service_id"] == "S002")
        assert s002["bm25_score"] == 3.0

    async def test_results_sorted_by_score_desc(self):
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session)
        scores = [r["bm25_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    async def test_union_of_service_ids_from_both_columns(self):
        """두 컬럼 결과의 합집합이 반환된다."""
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session)
        ids = {r["service_id"] for r in result}
        # SN 측 S001, S002 + MD 측 S002, S003
        assert ids == {"S001", "S002", "S003"}

    async def test_limit_applied_to_merged_result(self):
        session = _make_session(_SAMPLE_ROWS_SN, _SAMPLE_ROWS_MD)
        result = await bm25_search(["테니스"], session, limit=2)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# bm25_search — Bind parameter shape
# ---------------------------------------------------------------------------


class TestBm25SearchBindParams:
    async def test_each_query_uses_single_tok_bind(self):
        """각 쿼리는 단일 bind(:tok) 만 사용한다 (ParadeDB multi-clause 제약 우회)."""
        session = _make_session([], [], [], [])
        await bm25_search(["따릉이", "대여소"], session)
        token_values = []
        for call in session.execute.call_args_list:
            params = call[0][1]
            # 바인드 키는 정확히 'tok'과 'limit' 두 개여야 함
            assert set(params.keys()) == {"tok", "limit"}
            token_values.append(params["tok"])
        # 호출별로 토큰이 한 개씩 전달됨 (2 토큰 × 2 컬럼 = 4 호출)
        assert sorted(token_values) == sorted(["따릉이", "따릉이", "대여소", "대여소"])

    async def test_limit_in_bind(self):
        session = _make_session([])
        await bm25_search(["검색어"], session)
        for call in session.execute.call_args_list:
            assert call[0][1]["limit"] == BM25_LIMIT

    async def test_custom_limit_in_bind(self):
        session = _make_session([])
        await bm25_search(["수영"], session, limit=10)
        for call in session.execute.call_args_list:
            assert call[0][1]["limit"] == 10


# ---------------------------------------------------------------------------
# bm25_search — SQL safety
# ---------------------------------------------------------------------------


class TestBm25SearchSqlSafety:
    async def test_token_passed_as_bind_param_not_inlined(self):
        """SQL Injection 방지: 토큰은 sanitize 후 바인드 파라미터로만 전달된다."""
        raw = "'; DROP TABLE service_embeddings;"
        session = _make_session([])
        await bm25_search([raw], session)

        for call in session.execute.call_args_list:
            stmt, params = call[0][0], call[0][1]
            # tok 바인드 파라미터에 값 전달
            assert "tok" in params
            # DROP TABLE 문이 SQL 템플릿에 직접 삽입되지 않음
            assert "DROP TABLE" not in str(stmt)

    async def test_bm25_operator_in_sql(self):
        """SQL 에 ParadeDB BM25 연산자(@@@)가 포함된다."""
        captured: list = []

        async def _capture(stmt, params=None):
            captured.append(stmt)
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_score"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["검색어"], session)
        assert all("@@@" in str(s) for s in captured)

    async def test_columns_hardcoded_in_sql(self):
        """검색 컬럼명은 SQL 템플릿에 하드코딩되어 외부 입력의 영향을 받지 않는다."""
        captured: list = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_score"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["검색어"], session)
        # 두 번의 호출 중 하나는 service_name, 하나는 metadata 를 SQL에 포함
        joined = " ".join(captured)
        assert "service_name @@@" in joined
        assert "metadata @@@" in joined
