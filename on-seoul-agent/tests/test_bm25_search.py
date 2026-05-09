"""tools/bm25_search.py 단위 테스트.

Mock DB 세션으로 BM25 쿼리 변환, bind 파라미터, 반환 형식을 검증한다.
실제 DB 및 ParadeDB 없이 동작한다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.bm25_search import BM25_LIMIT, bm25_search, build_bm25_query


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

    def test_multiple_tokens_joined_with_space(self):
        """복수 토큰은 공백으로 연결된다."""
        assert build_bm25_query(["따릉이", "대여소"]) == "따릉이 대여소"

    def test_empty_tokens_returns_empty_string(self):
        """빈 토큰 리스트는 빈 문자열을 반환한다."""
        assert build_bm25_query([]) == ""


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
        """공백 연결된 토큰 문자열이 bind 파라미터로 전달된다."""
        session = _make_session([])
        await bm25_search(["따릉이", "대여소"], session)
        bind = session.execute.call_args[0][1]
        assert bind["query"] == "따릉이 대여소"

    async def test_limit_in_bind(self):
        """LIMIT 파라미터가 bind에 포함된다."""
        session = _make_session([])
        await bm25_search(["검색어"], session)
        bind = session.execute.call_args[0][1]
        assert bind["limit"] == BM25_LIMIT

    async def test_single_token_bind(self):
        """단일 토큰도 bind query에 정상 전달된다."""
        session = _make_session([])
        await bm25_search(["수영"], session)
        bind = session.execute.call_args[0][1]
        assert bind["query"] == "수영"

    async def test_custom_limit_in_bind(self):
        """limit 파라미터를 명시하면 bind에 해당 값이 전달된다."""
        session = _make_session([])
        await bm25_search(["수영"], session, limit=10)
        bind = session.execute.call_args[0][1]
        assert bind["limit"] == 10


class TestBm25SearchSqlSafety:
    async def test_token_values_passed_as_bind_params(self):
        """SQL Injection 방지: 악성 입력이 bind 파라미터로만 전달되고 SQL 템플릿에 삽입되지 않는다."""
        malicious = "'; DROP TABLE service_embeddings; --"
        session = _make_session([])
        await bm25_search([malicious], session)

        # session.execute의 두 번째 인자(params dict)에서 바인드 파라미터를 확인한다.
        call_args = session.execute.call_args
        stmt, params = call_args[0][0], call_args[0][1]

        # 바인드 파라미터로 정상 전달됨
        assert params["query"] == malicious
        # SQL 템플릿 문자열에는 삽입되지 않음
        assert malicious not in str(stmt)

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
