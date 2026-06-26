"""tools/bm25_search.py 단위 테스트.

Mock DB 세션으로 BM25 쿼리 변환, bind 파라미터, 머지 동작을 검증한다.
실제 DB 및 ParadeDB 없이 동작한다.

테스트 구조:
- build_bm25_query: 토큰 sanitize/OR 결합 단위 함수
- bm25_search: service_name 단일 컬럼 토큰별 호출 후 머지 동작.
  metadata 컬럼은 json_fields 색인이라 평문 토큰이 0건 매칭하고, field-qualified
  방향은 봉인 평가셋에서 recall 을 떨어뜨려 _BM25_COLUMNS 에서 제외했다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.bm25_search import BM25_LIMIT, bm25_search, build_bm25_query


def _make_session(*responses: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출마다 responses 순서대로 row 리스트를 반환한다.

    bm25_search 가 service_name 단일 컬럼을 토큰별로 순차 호출하므로 (토큰 수만큼)
    호출 횟수만큼 응답을 미리 준비한다. 응답이 1개만 주어지면 모든 호출에 동일하게
    반환된다.
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


# ParadeDB가 BM25 relevance 순으로 결과를 반환하면 ROW_NUMBER 로 rank 부여.
# bm25_score 는 Python 사이드에서 1.0/rank 로 산출됨.
# 단일 컬럼(service_name) 구조에서 머지는 토큰별 결과 합집합으로 일어난다.
# 두 토큰의 결과를 모델링한다 (토큰1 → _SAMPLE_ROWS_T1, 토큰2 → _SAMPLE_ROWS_T2).
_SAMPLE_ROWS_T1 = [
    {"service_id": "S001", "service_name": "테니스장1", "bm25_rank": 1},
    {"service_id": "S002", "service_name": "테니스장2", "bm25_rank": 2},
]
_SAMPLE_ROWS_T2 = [
    {"service_id": "S002", "service_name": "테니스장2", "bm25_rank": 1},
    {"service_id": "S003", "service_name": "수영장1", "bm25_rank": 2},
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
    async def test_single_token_executes_one_query(self):
        """단일 토큰 × 1 컬럼(service_name) = 1회 execute."""
        session = _make_session(_SAMPLE_ROWS_T1)
        await bm25_search(["테니스"], session)
        assert session.execute.call_count == 1

    async def test_two_tokens_execute_two_queries(self):
        """2 토큰 × 1 컬럼(service_name) = 2회 execute."""
        session = _make_session([], [])
        await bm25_search(["테니스", "예약"], session)
        assert session.execute.call_count == 2

    async def test_returns_list_of_dicts(self):
        session = _make_session(_SAMPLE_ROWS_T1)
        result = await bm25_search(["테니스"], session)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)

    async def test_result_has_required_keys(self):
        session = _make_session(_SAMPLE_ROWS_T1)
        result = await bm25_search(["테니스"], session)
        for row in result:
            assert "service_id" in row
            assert "service_name" in row
            assert "bm25_score" in row

    async def test_empty_results_in_all_queries(self):
        session = _make_session([])
        result = await bm25_search(["없는키워드"], session)
        assert result == []


# ---------------------------------------------------------------------------
# bm25_search — Merge by MAX(bm25_score)
# ---------------------------------------------------------------------------


class TestBm25SearchMerge:
    """단일 컬럼(service_name)에서 토큰별 결과를 머지하는 동작 검증.

    두 토큰의 결과(_SAMPLE_ROWS_T1 / _SAMPLE_ROWS_T2)를 합집합·MAX score 로 머지한다.
    """

    async def test_duplicate_service_id_takes_max_score(self):
        """두 토큰 모두에 매칭된 service_id 는 최대 점수(=최소 rank)가 채택된다.

        S002: 토큰1 rank=2 (score 0.5), 토큰2 rank=1 (score 1.0) → 1.0 채택.
        """
        session = _make_session(_SAMPLE_ROWS_T1, _SAMPLE_ROWS_T2)
        result = await bm25_search(["테니스", "예약"], session)
        s002 = next(r for r in result if r["service_id"] == "S002")
        assert s002["bm25_score"] == 1.0

    async def test_results_sorted_by_score_desc(self):
        session = _make_session(_SAMPLE_ROWS_T1, _SAMPLE_ROWS_T2)
        result = await bm25_search(["테니스", "예약"], session)
        scores = [r["bm25_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    async def test_union_of_service_ids_from_both_tokens(self):
        """두 토큰 결과의 합집합이 반환된다."""
        session = _make_session(_SAMPLE_ROWS_T1, _SAMPLE_ROWS_T2)
        result = await bm25_search(["테니스", "예약"], session)
        ids = {r["service_id"] for r in result}
        # 토큰1 측 S001, S002 + 토큰2 측 S002, S003
        assert ids == {"S001", "S002", "S003"}

    async def test_limit_applied_to_merged_result(self):
        session = _make_session(_SAMPLE_ROWS_T1, _SAMPLE_ROWS_T2)
        result = await bm25_search(["테니스", "예약"], session, limit=2)
        assert len(result) == 2

    async def test_bm25_score_derived_from_rank(self):
        """bm25_score 는 1.0/rank 로 산출된다 (rank=1 → score=1.0)."""
        session = _make_session(_SAMPLE_ROWS_T1, _SAMPLE_ROWS_T2)
        result = await bm25_search(["테니스", "예약"], session)
        # S001 은 토큰1 단독, rank=1 → score=1.0
        s001 = next(r for r in result if r["service_id"] == "S001")
        assert s001["bm25_score"] == 1.0
        # S003 은 토큰2 단독, rank=2 → score=0.5
        s003 = next(r for r in result if r["service_id"] == "S003")
        assert s003["bm25_score"] == 0.5


# ---------------------------------------------------------------------------
# bm25_search — Bind parameter shape
# ---------------------------------------------------------------------------


class TestBm25SearchInlineSql:
    """ParadeDB 0.23.4 가 prepared statement(@@@ $N)를 지원하지 않으므로
    토큰을 SQL 에 직접 인라인한다. 인라인 안전성·SQL 형태를 검증한다.
    """

    async def test_tokens_inlined_in_sql(self):
        """sanitize 된 토큰이 single quote 로 감싸여 SQL 에 직접 삽입된다."""
        session = _make_session([], [], [], [])
        await bm25_search(["따릉이", "대여소"], session)
        # call_args[0] 은 (stmt,) 단일 요소 튜플 — bind dict 없음
        all_sql = " ".join(str(call[0][0]) for call in session.execute.call_args_list)
        assert "'따릉이'" in all_sql
        assert "'대여소'" in all_sql

    async def test_no_bind_params_passed_to_execute(self):
        """execute 호출 시 bind dict 를 전달하지 않는다 (prepared statement 회피)."""
        session = _make_session([])
        await bm25_search(["검색어"], session)
        for call in session.execute.call_args_list:
            args = call[0]
            # 인자는 stmt 1개만 (bind dict 없음)
            assert len(args) == 1

    async def test_limit_inlined_in_sql(self):
        session = _make_session([])
        await bm25_search(["검색어"], session)
        for call in session.execute.call_args_list:
            assert f"LIMIT {BM25_LIMIT}" in str(call[0][0])

    async def test_custom_limit_inlined(self):
        session = _make_session([])
        await bm25_search(["수영"], session, limit=10)
        for call in session.execute.call_args_list:
            assert "LIMIT 10" in str(call[0][0])


# ---------------------------------------------------------------------------
# bm25_search — SQL safety
# ---------------------------------------------------------------------------


class TestBm25SearchSqlSafety:
    async def test_sql_injection_neutralized_by_strict_sanitize(self):
        """SQL Injection 시도: strict 화이트리스트(Hangul + alphanumeric)로
        single quote, semicolon, 공백 등 SQL meta 문자가 전부 제거된다.

        ' ; - 가 제거된 후 'DROPTABLEx' 형태로 남으므로 무력화된다.
        """
        raw = "'; DROP TABLE service_embeddings;"
        session = _make_session([])
        await bm25_search([raw], session)

        for call in session.execute.call_args_list:
            stmt = str(call[0][0])
            # SQL meta 문자가 인라인 부분에 포함되지 않음
            assert "; DROP" not in stmt
            assert "DROP TABLE service_embeddings" not in stmt
            # 원본의 single quote / semicolon 가 그대로 SQL 에 삽입되지 않음
            assert "'; " not in stmt

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
        """검색 컬럼은 service_name 단일 컬럼으로 SQL 템플릿에 하드코딩된다.

        metadata 컬럼은 json_fields 색인이라 평문 토큰이 매칭되지 않아 제외했다.
        SQL 에 metadata @@@ 가 나타나지 않는지 회귀 가드.
        """
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
        joined = " ".join(captured)
        assert "service_name @@@" in joined
        assert "metadata @@@" not in joined

    async def test_all_sql_meta_chars_stripped(self):
        """SQL meta 문자가 인라인 부분에 일체 포함되지 않는다 (enum 검증)."""
        raw_inputs = [
            "O'Brien",  # single quote
            "수영; --",  # comment / semicolon
            "수영 OR 1=1",  # space + reserved word
            "/* comment */수영",  # block comment
            "수영\n장",  # newline
            "수영\\장",  # backslash
        ]
        session = _make_session([])
        for raw in raw_inputs:
            session.execute.reset_mock()
            await bm25_search([raw], session)
            for call in session.execute.call_args_list:
                stmt = str(call[0][0])
                # 토큰이 인라인되는 부분만 추출 — WHERE col @@@ '...'
                for meta in ["'", ";", "--", "/*", "*/", "\\"]:
                    # SQL 메타 문자가 토큰 인라인 단계에서 잔류하지 않는지
                    # (SQL 템플릿 자체의 single quote 는 토큰을 감싸는 ' ' 뿐)
                    if meta == "'":
                        # single quote 는 토큰을 감싸는 'token' 2개 +
                        # row_kind = 'identity' predicate 2개 = 4개만 정상.
                        assert stmt.count("'") <= 4
                    else:
                        assert meta not in stmt


class TestBm25SearchPartialIndexPredicate:
    """Fix 2 회귀 가드 — partial bm25 인덱스(WHERE row_kind='identity') 적중을 위한
    `AND row_kind = 'identity'` predicate 가 모든 토큰·컬럼 쿼리에 포함되는지 검증한다.

    이 조건이 누락되면 planner 가 partial 인덱스를 후보에서 제외해 Parallel Seq Scan
    으로 떨어진다(성능 ~9배 저하). 결과 동등성과 무관한 순수 성능 회귀이므로 단위
    테스트로 고정한다.
    """

    async def test_row_kind_identity_predicate_in_every_query(self):
        """모든 토큰×컬럼 쿼리에 AND row_kind = 'identity' 가 포함된다."""
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["테니스", "예약"], session)
        # 2 토큰 × 1 컬럼(service_name) = 2 쿼리, 모두에 predicate 포함.
        assert len(captured) == 2
        for sql in captured:
            normalized = " ".join(sql.split())
            assert "row_kind = 'identity'" in normalized, (
                "partial bm25 인덱스 적중을 위한 row_kind predicate 누락"
            )

    async def test_row_kind_predicate_anded_after_bm25_operator(self):
        """row_kind 조건이 @@@ 매칭 뒤에 AND 로 결합된다 (인덱스 predicate 정렬)."""
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["수영"], session)
        for sql in captured:
            normalized = " ".join(sql.split())
            op_pos = normalized.find("@@@")
            rk_pos = normalized.find("row_kind = 'identity'")
            assert op_pos != -1 and rk_pos != -1
            assert rk_pos > op_pos, "row_kind 조건이 @@@ 뒤 AND 절에 와야 함"


class TestBm25SearchRelevanceOrdering:
    """relevance ORDER BY 회귀 가드 — BM25 토큰 쿼리가 `paradedb.score(id)` 기준
    relevance 순으로 정렬되어 LIMIT 컷이 결정적·고관련도 우선이 되는지 검증한다.

    과거 결함: ORDER BY 부재로 `ROW_NUMBER() OVER ()` 가 물리 스캔 순서를 rank 로
    사용해, 매칭 > LIMIT 인 고빈도 토큰(예: 테니스장 421건 > LIMIT 50)에서 잘려나가는
    50건이 스캔 경로(Seq vs ParadeDB Custom Scan)에 따라 달라졌다(라이브 실측:
    relevance top-50 중 29건 누락). 이 테스트군이 그 회귀를 고정한다.
    """

    async def test_query_has_relevance_order_by(self):
        """모든 토큰×컬럼 쿼리에 `ORDER BY paradedb.score(id) DESC` 가 포함된다."""
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["테니스"], session)
        assert captured, "쿼리가 실행되지 않았다"
        for sql in captured:
            normalized = " ".join(sql.split()).upper()
            assert "ORDER BY" in normalized, (
                "LIMIT 컷이 결정적이려면 relevance/안정 ORDER BY 가 필요하다"
            )
            assert "PARADEDB.SCORE(ID) DESC" in normalized, (
                "relevance top-N 보장을 위해 paradedb.score(id) DESC 정렬이 필요하다"
            )

    async def test_order_by_has_deterministic_tie_break(self):
        """점수 동률 시 결정적 정렬을 위해 service_id ASC tie-break 가 ORDER BY 에 포함된다."""
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["테니스"], session)
        for sql in captured:
            normalized = " ".join(sql.split()).upper()
            # score DESC 뒤에 service_id ASC tie-break 가 와야 함
            assert "PARADEDB.SCORE(ID) DESC, SERVICE_ID ASC" in normalized, (
                "점수 동률 결정성을 위해 service_id ASC tie-break 가 필요하다"
            )

    async def test_order_by_appears_in_both_window_and_outer_clause(self):
        """ROW_NUMBER 윈도우와 외부 LIMIT 절 모두 동일 relevance ORDER BY 를 사용한다.

        rank(윈도우 정렬)와 실제 반환 순서(외부 LIMIT 정렬)가 일치해야 rank 가
        relevance 순서를 정확히 반영한다.
        """
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["테니스"], session)
        for sql in captured:
            normalized = " ".join(sql.split()).upper()
            # "PARADEDB.SCORE(ID) DESC, SERVICE_ID ASC" 가 2회 (윈도우 + 외부) 등장
            assert normalized.count("PARADEDB.SCORE(ID) DESC, SERVICE_ID ASC") == 2, (
                "ROW_NUMBER 윈도우와 외부 ORDER BY 가 동일 정렬을 써야 rank 가 정합적이다"
            )

    async def test_repeated_calls_produce_identical_sql(self):
        """동일 입력 반복 시 동일 SQL 이 생성된다 (쿼리 빌더 결정성)."""
        captured: list[str] = []

        async def _capture(stmt, params=None):
            captured.append(str(stmt))
            mr = MagicMock()
            mr.keys.return_value = ["service_id", "service_name", "bm25_rank"]
            mr.fetchall.return_value = []
            return mr

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["테니스", "예약"], session)
        first_batch = list(captured)
        captured.clear()
        await bm25_search(["테니스", "예약"], session)
        second_batch = list(captured)
        assert first_batch == second_batch, "동일 입력은 동일 SQL 을 생성해야 한다"


class TestBm25SearchGuards:
    """방어적 가드 (limit, 빈 token, 토큰 상한·길이 상한) 검증."""

    async def test_negative_limit_coerced_to_at_least_1(self):
        """limit <= 0 도 LIMIT 1 로 보정 (의도 외 SQL 방지)."""
        session = _make_session([])
        await bm25_search(["수영"], session, limit=-5)
        for call in session.execute.call_args_list:
            assert "LIMIT 1" in str(call[0][0])

    async def test_zero_limit_coerced(self):
        session = _make_session([])
        await bm25_search(["수영"], session, limit=0)
        for call in session.execute.call_args_list:
            assert "LIMIT 1" in str(call[0][0])

    async def test_max_tokens_cap_applied(self):
        """토큰 9개 입력 시 상한 8개로 잘려 8쿼리(1컬럼×8토큰)만 실행."""
        session = _make_session(*[[] for _ in range(8)])
        # 9 토큰 — 8개로 잘림
        tokens = [f"토큰{i}" for i in range(9)]
        await bm25_search(tokens, session)
        # 컬럼 1(service_name) × 토큰 8 = 8 쿼리
        assert session.execute.call_count == 8

    async def test_long_token_truncated(self):
        """64자 초과 토큰은 잘려서 인라인된다."""
        long_token = "수" * 100  # 100자 Hangul
        session = _make_session([])
        await bm25_search([long_token], session)
        for call in session.execute.call_args_list:
            stmt = str(call[0][0])
            # 토큰이 64자로 잘림
            assert "'" + ("수" * 64) + "'" in stmt
            assert "'" + ("수" * 100) + "'" not in stmt

    async def test_deterministic_tie_break_by_service_id(self):
        """점수 동률 시 service_id 오름차순으로 정렬된다 (결정적 tie-break)."""
        # 두 토큰 모두에서 rank=1 인 결과 → score=1.0 동률
        t1 = [
            {"service_id": "S_B", "service_name": "B", "bm25_rank": 1},
        ]
        t2 = [
            {"service_id": "S_A", "service_name": "A", "bm25_rank": 1},
        ]
        session = _make_session(t1, t2)
        result = await bm25_search(["테니스", "예약"], session)
        # 동률(1.0) 두 개 → service_id 오름차순
        assert [r["service_id"] for r in result] == ["S_A", "S_B"]
