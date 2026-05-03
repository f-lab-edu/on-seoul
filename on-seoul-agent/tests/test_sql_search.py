"""tools/sql_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB에 접근하지 않는다.
"""

from unittest.mock import AsyncMock, MagicMock

from tools.sql_search import TOP_K, sql_search


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = ["service_id", "service_name"]
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


_SAMPLE_ROWS = [
    {
        "service_id": "S001",
        "service_name": "마포 수영장",
        "area_name": "마포구",
        "service_status": "접수중",
    }
]


class TestSqlSearchBasic:
    async def test_returns_list_of_dicts(self):
        """기본 조회 결과가 리스트로 반환된다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await sql_search(session)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["service_id"] == "S001"

    async def test_empty_result_returns_empty_list(self):
        """결과가 없을 때 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await sql_search(session)
        assert result == []

    async def test_deleted_at_is_null_always_in_where(self):
        """deleted_at IS NULL 조건은 항상 WHERE 절에 포함된다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await sql_search(session)
        assert "deleted_at IS NULL" in executed_sqls[0]


class TestSqlSearchFilters:
    async def test_max_class_name_in_bind(self):
        """max_class_name 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await sql_search(session, max_class_name="체육시설")
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "체육시설"

    async def test_area_name_in_bind(self):
        """area_name 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await sql_search(session, area_name="마포구")
        bind = session.execute.call_args[0][1]
        assert bind["area_name"] == "마포구"

    async def test_service_status_in_bind(self):
        """service_status 필터가 bind 파라미터에 포함된다."""
        session = _make_session([])
        await sql_search(session, service_status="접수중")
        bind = session.execute.call_args[0][1]
        assert bind["service_status"] == "접수중"

    async def test_keyword_wrapped_with_ilike_pattern(self):
        """keyword는 ILIKE 패턴(%%keyword%%)으로 변환된다."""
        session = _make_session([])
        await sql_search(session, keyword="수영")
        bind = session.execute.call_args[0][1]
        assert bind["keyword"] == "%수영%"

    async def test_keyword_uses_coalesce_concat_expression(self):
        """keyword 조건이 idx_psr_trgm_name_combined 인덱스 식과 일치하는 COALESCE 연결 표현식을 사용한다.

        OR 절(두 컬럼 개별 ILIKE)은 BitmapOr 비용 추정 실패로 GIN 인덱스를 무시하므로,
        단일 COALESCE 연결 표현식으로 쿼리를 구성한다.
        """
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await sql_search(session, keyword="수영")

        sql_text = executed_sqls[0]
        assert "COALESCE" in sql_text
        assert "ILIKE" in sql_text
        # OR 절 방식이 아님을 검증
        assert "service_name ILIKE" not in sql_text
        assert "place_name ILIKE" not in sql_text

    async def test_no_filter_excludes_optional_keys(self):
        """필터 없이 호출하면 bind에 선택적 키가 없다."""
        session = _make_session([])
        await sql_search(session)
        bind = session.execute.call_args[0][1]
        assert "max_class_name" not in bind
        assert "area_name" not in bind
        assert "service_status" not in bind
        assert "keyword" not in bind

    async def test_top_k_in_bind_default(self):
        """top_k 기본값이 bind에 포함된다."""
        session = _make_session([])
        await sql_search(session)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == TOP_K

    async def test_custom_top_k_override(self):
        """top_k=5 전달 시 bind에 반영된다."""
        session = _make_session([])
        await sql_search(session, top_k=5)
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == 5


class TestSqlSearchAllFilters:
    async def test_all_filters_combined_in_bind(self):
        """모든 필터를 동시에 전달하면 bind에 모두 포함된다."""
        session = _make_session([])
        await sql_search(
            session,
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
            keyword="수영",
        )
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "체육시설"
        assert bind["area_name"] == "강남구"
        assert bind["service_status"] == "접수중"
        assert bind["keyword"] == "%수영%"

    async def test_all_filters_appear_in_sql_text(self):
        """모든 필터를 전달하면 SQL 문자열에 관련 조건 절이 포함된다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)

        await sql_search(
            session,
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
            keyword="수영",
        )

        sql_text = executed_sqls[0]
        assert "max_class_name" in sql_text
        assert "area_name" in sql_text
        assert "service_status" in sql_text
        assert "ILIKE" in sql_text


class TestSqlSearchSqlInjection:
    async def test_malicious_value_not_in_sql_text(self):
        """SQL Injection 방지: 악성 값이 SQL 문자열에 직접 삽입되지 않는다."""
        injected_values = [
            "'; DROP TABLE public_service_reservations; --",
            "' OR '1'='1",
        ]

        for bad_value in injected_values:
            executed_sqls: list[str] = []

            async def _capture(stmt, params=None, _sqls=executed_sqls):
                _sqls.append(str(stmt))
                m = MagicMock()
                m.keys.return_value = []
                m.fetchall.return_value = []
                return m

            session = MagicMock()
            session.execute = AsyncMock(side_effect=_capture)

            await sql_search(
                session,
                max_class_name=bad_value,
                area_name=bad_value,
                keyword=bad_value,
            )

            sql_text = executed_sqls[0]
            assert bad_value not in sql_text, (
                f"SQL Injection 위험: 값 '{bad_value}'이 SQL 문자열에 삽입됨"
            )

    async def test_keyword_injection_value_only_in_bind(self):
        """keyword의 악성 값은 bind 파라미터로만 전달되고, LIKE 와일드카드는 이스케이프된다."""
        from tools.sql_search import _escape_like

        malicious = "'; DROP TABLE public_service_reservations; --"
        session = _make_session([])
        await sql_search(session, keyword=malicious)

        bind = session.execute.call_args[0][1]
        # _escape_like 처리 후 %...% 래핑되어야 한다
        assert bind["keyword"] == f"%{_escape_like(malicious)}%"
