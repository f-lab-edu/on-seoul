"""tools/bm25_search.py 단위 테스트.

Mock DB 세션으로 BM25 쿼리 변환, bind 파라미터, 반환 형식을 검증한다.
실제 DB 및 ParadeDB 없이 동작한다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.bm25_search import BM25_LIMIT, bm25_search, build_bm25_query, build_field_queries


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = ["service_id", "bm25_score"]
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


_SAMPLE_ROWS = [
    {"service_id": "S001", "bm25_score": 2.5},
    {"service_id": "S002", "bm25_score": 1.8},
]


class TestBuildBm25Query:
    def test_single_token(self):
        """단일 토큰은 그대로 반환된다."""
        assert build_bm25_query(["따릉이"]) == "따릉이"

    def test_multiple_tokens_joined_with_or(self):
        """복수 토큰은 OR로 연결된다."""
        assert build_bm25_query(["따릉이", "대여소"]) == "따릉이 OR 대여소"

    def test_empty_tokens_returns_empty_string(self):
        """빈 토큰 리스트는 빈 문자열을 반환한다."""
        assert build_bm25_query([]) == ""

    def test_special_chars_removed(self):
        """Tantivy 특수문자(*, ~, ^, ", (, ), {, }, [, ])가 토큰에서 제거된다."""
        assert build_bm25_query(["체육*관"]) == "체육관"
        assert build_bm25_query(["수영~장"]) == "수영장"
        assert build_bm25_query(['"따릉이"']) == "따릉이"
        assert build_bm25_query(["(공원)"]) == "공원"
        assert build_bm25_query(["[강당]"]) == "강당"
        assert build_bm25_query(["{센터}"]) == "센터"
        assert build_bm25_query(["접수^중"]) == "접수중"

    def test_reserved_words_filtered_uppercase(self):
        """대문자 Tantivy 예약어(AND, OR, NOT, TO, IN)는 필터링된다."""
        assert build_bm25_query(["AND"]) == ""
        assert build_bm25_query(["OR"]) == ""
        assert build_bm25_query(["NOT"]) == ""
        assert build_bm25_query(["TO"]) == ""
        assert build_bm25_query(["IN"]) == ""

    def test_reserved_words_filtered_lowercase(self):
        """소문자 예약어도 대소문자 무관하게 필터링된다."""
        assert build_bm25_query(["and"]) == ""
        assert build_bm25_query(["or"]) == ""
        assert build_bm25_query(["not"]) == ""
        assert build_bm25_query(["to"]) == ""
        assert build_bm25_query(["in"]) == ""

    def test_reserved_words_mixed_case_filtered(self):
        """혼합 대소문자 예약어(And, Or, Not ...)도 필터링된다."""
        assert build_bm25_query(["And"]) == ""
        assert build_bm25_query(["Or"]) == ""
        assert build_bm25_query(["Not"]) == ""

    def test_reserved_words_removed_from_token_list(self):
        """예약어가 포함된 리스트에서 예약어만 제거되고 나머지 토큰은 유지된다."""
        result = build_bm25_query(["수영", "AND", "강습"])
        assert result == "수영 OR 강습"

    def test_special_chars_and_reserved_word_combined(self):
        """특수문자 제거 후 예약어가 되는 토큰도 필터링된다."""
        # "AND*" → 특수문자 제거 → "AND" → 예약어 필터링
        assert build_bm25_query(["AND*"]) == ""
        # "(OR)" → 특수문자 제거 → "OR" → 예약어 필터링
        assert build_bm25_query(["(OR)"]) == ""

    def test_token_becomes_empty_after_special_char_removal(self):
        """특수문자만으로 구성된 토큰은 제거 후 빈 문자열이 되어 결과에 포함되지 않는다."""
        assert build_bm25_query(["***"]) == ""
        assert build_bm25_query(["***", "수영"]) == "수영"

    def test_plus_minus_removed(self):
        """Tantivy 필수/제외 연산자(+, -)가 토큰에서 제거된다."""
        assert build_bm25_query(["+수영"]) == "수영"
        assert build_bm25_query(["-수영"]) == "수영"

    def test_colon_removed(self):
        """필드 한정 구분자(:)가 토큰에서 제거된다."""
        assert build_bm25_query(["service:name"]) == "servicename"

    def test_backslash_removed(self):
        """이스케이프 문자(\\)가 토큰에서 제거된다."""
        assert build_bm25_query(["수영\\장"]) == "수영장"

    def test_question_mark_removed(self):
        """와일드카드 문자(?)가 토큰에서 제거된다."""
        assert build_bm25_query(["수영?장"]) == "수영장"


class TestBuildFieldQueries:
    def test_single_token_no_parentheses(self):
        """단일 토큰은 괄호 없이 'field:token' 형태."""
        sn, md = build_field_queries(["테니스장"])
        assert sn == "service_name:테니스장"
        assert md == "metadata:테니스장"

    def test_multiple_tokens_or_grouped(self):
        """복수 토큰은 '(tok1 OR tok2)' 로 감싼다."""
        sn, md = build_field_queries(["테니스장", "예약", "방법"])
        assert sn == "service_name:(테니스장 OR 예약 OR 방법)"
        assert md == "metadata:(테니스장 OR 예약 OR 방법)"

    def test_empty_returns_none(self):
        """유효 토큰 없으면 None."""
        assert build_field_queries([]) is None
        assert build_field_queries(["AND", "OR"]) is None


class TestBm25SearchEmptyQueryGuard:
    async def test_all_reserved_tokens_returns_empty_list_without_db(self):
        """모든 토큰이 예약어면 DB 호출 없이 빈 리스트를 반환한다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await bm25_search(["AND", "OR", "NOT"], session)
        assert result == []
        session.execute.assert_not_called()

    async def test_all_special_char_tokens_returns_empty_list_without_db(self):
        """모든 토큰이 특수문자만이면 DB 호출 없이 빈 리스트를 반환한다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await bm25_search(["***", "++", "---"], session)
        assert result == []
        session.execute.assert_not_called()


class TestBm25SearchBasic:
    async def test_returns_list_of_dicts(self):
        """bm25_search는 list[dict]를 반환한다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await bm25_search(["따릉이"], session)
        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)

    async def test_result_has_required_keys(self):
        """반환 dict는 service_id, bm25_score 키를 포함한다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await bm25_search(["따릉이"], session)
        assert len(result) == 2
        assert result[0]["service_id"] == "S001"
        assert result[0]["bm25_score"] == 2.5

    async def test_empty_result_returns_empty_list(self):
        """결과가 없으면 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await bm25_search(["없는키워드"], session)
        assert result == []

    async def test_empty_tokens_returns_empty_list(self):
        """빈 토큰 리스트는 DB를 호출하지 않고 빈 리스트를 반환한다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await bm25_search([], session)
        assert result == []
        session.execute.assert_not_called()


class TestBm25SearchBindParams:
    async def test_query_string_in_bind(self):
        """필드-스코프 쿼리 문자열이 query_sn / query_md 로 분리되어 bind에 전달된다."""
        session = _make_session([])
        await bm25_search(["따릉이", "대여소"], session)
        bind = session.execute.call_args[0][1]
        assert bind["query_sn"] == "service_name:(따릉이 OR 대여소)"
        assert bind["query_md"] == "metadata:(따릉이 OR 대여소)"

    async def test_limit_in_bind(self):
        """LIMIT 파라미터가 bind에 포함된다."""
        session = _make_session([])
        await bm25_search(["검색어"], session)
        bind = session.execute.call_args[0][1]
        assert bind["limit"] == BM25_LIMIT

    async def test_single_token_bind(self):
        """단일 토큰은 괄호 없이 필드-스코프 형태로 전달된다."""
        session = _make_session([])
        await bm25_search(["수영"], session)
        bind = session.execute.call_args[0][1]
        assert bind["query_sn"] == "service_name:수영"
        assert bind["query_md"] == "metadata:수영"

    async def test_custom_limit_in_bind(self):
        """limit 파라미터를 명시하면 bind에 해당 값이 전달된다."""
        session = _make_session([])
        await bm25_search(["수영"], session, limit=10)
        bind = session.execute.call_args[0][1]
        assert bind["limit"] == 10


class TestBm25SearchSqlSafety:
    async def test_token_values_passed_as_bind_params(self):
        """SQL Injection 방지: 입력값은 sanitize 후 필드-스코프 문자열로 조립되어
        bind 파라미터(query_sn/query_md)로만 전달되고 SQL 템플릿에 삽입되지 않는다.

        특수문자(')가 포함된 입력을 사용한다.
        Tantivy 특수문자 제거 후 build_field_queries 에서 필드-스코프로 감싼다.
        """
        raw = "'; DROP TABLE service_embeddings;"
        # 특수문자 중 sanitize 대상이 아닌 '' 가 남아 서비스명 부분에 포함됨
        session = _make_session([])
        await bm25_search([raw], session)

        call_args = session.execute.call_args
        stmt, params = call_args[0][0], call_args[0][1]

        # bind 파라미터에 query_sn/query_md 키가 존재함
        assert "query_sn" in params
        assert "query_md" in params
        # 원본 DROP 문이 SQL 템플릿에 삽입되지 않음
        assert "DROP TABLE" not in str(stmt)

    async def test_bm25_operator_in_sql(self):
        """SQL에 ParadeDB BM25 연산자(@@@)가 포함된다."""
        executed_stmts: list = []

        async def _capture(stmt, params=None):
            executed_stmts.append(stmt)
            mock_result = MagicMock()
            mock_result.keys.return_value = ["service_id", "bm25_score"]
            mock_result.fetchall.return_value = []
            return mock_result

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await bm25_search(["검색어"], session)

        assert "@@@" in str(executed_stmts[0])
