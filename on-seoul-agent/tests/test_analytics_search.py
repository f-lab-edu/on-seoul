"""tools/analytics_search.py 단위 테스트.

Mock DB 세션으로 집계 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB에 접근하지 않는다.

핵심 검증:
- GROUP BY 차원 화이트리스트 방어 (인젝션 차단).
- count metric 정렬(count DESC), distinct metric 정렬.
- keyword ILIKE 이스케이프(%/_/\\) — sql_search 와 동일 패턴 재사용.
- 필터 조합(max_class_name/area_name/service_status)이 bind 로만 전달.
- 빈 결과 [].
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tools.analytics_search import _DIMENSION_COLUMNS, analytics_search


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = ["group_value", "count"]
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _capturing_session() -> tuple[MagicMock, list[str]]:
    """실행된 SQL 문자열을 캡처하는 fake 세션."""
    executed: list[str] = []

    async def _capture(stmt, params=None):
        executed.append(str(stmt))
        m = MagicMock()
        m.keys.return_value = ["group_value", "count"]
        m.fetchall.return_value = []
        return m

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_capture)
    return session, executed


class TestAnalyticsSearchWhitelist:
    async def test_unknown_group_by_raises(self):
        """화이트리스트 외 group_by 는 명시적으로 방어한다 (인젝션 차단)."""
        session = _make_session([])
        with pytest.raises((ValueError, KeyError)):
            await analytics_search(
                session,
                group_by="service_name; DROP TABLE x",
                metric="count",
            )

    async def test_no_sql_executed_on_invalid_dimension(self):
        """방어 시 SQL 을 실행하지 않는다 (DB 도달 전 차단)."""
        session, executed = _capturing_session()
        with pytest.raises((ValueError, KeyError)):
            await analytics_search(session, group_by="bad_col", metric="count")
        assert executed == []

    @pytest.mark.parametrize(
        "evil_group_by",
        [
            "max_class_name; DROP TABLE public_service_reservations",
            "1=1",
            "area_name OR 1=1",
            "area_name --",
            "*",
            "",
            "AREA_NAME",  # 대소문자 불일치 — 화이트리스트 정확 일치만 허용
        ],
    )
    async def test_injection_group_by_blocked_before_db(self, evil_group_by):
        """화이트리스트 외 임의 group_by 는 session.execute 도달 전 차단된다."""
        session, executed = _capturing_session()
        with pytest.raises((ValueError, KeyError)):
            await analytics_search(
                session, group_by=evil_group_by, metric="count"
            )
        # DB 도달 전 차단 — execute 자체가 호출되지 않아야 한다.
        session.execute.assert_not_called()
        assert executed == []

    async def test_only_whitelisted_column_in_sql(self):
        """GROUP BY 컬럼은 화이트리스트 dict 값만 SQL 에 삽입된다."""
        for key, column in _DIMENSION_COLUMNS.items():
            session, executed = _capturing_session()
            await analytics_search(session, group_by=key, metric="count")
            assert f"GROUP BY {column}" in executed[0]


class TestAnalyticsSearchCount:
    async def test_count_returns_group_value_and_count(self):
        """count metric 은 group_value/count 매핑을 반환한다."""
        rows = [
            {"group_value": "강서구", "count": 7},
            {"group_value": "강남구", "count": 3},
        ]
        session = _make_session(rows)
        result = await analytics_search(
            session, group_by="area_name", metric="count"
        )
        assert result == rows

    async def test_count_order_by_count_desc(self):
        """count metric 은 count DESC 정렬한다."""
        session, executed = _capturing_session()
        await analytics_search(session, group_by="area_name", metric="count")
        assert "ORDER BY count DESC" in executed[0]

    async def test_count_select_count_star(self):
        """count metric SQL 에 COUNT(*) AS count 가 포함된다."""
        session, executed = _capturing_session()
        await analytics_search(session, group_by="area_name", metric="count")
        assert "COUNT(*)" in executed[0]

    async def test_empty_result_returns_empty_list(self):
        """결과가 없으면 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await analytics_search(
            session, group_by="area_name", metric="count"
        )
        assert result == []


class TestAnalyticsSearchDistinct:
    async def test_distinct_select_distinct(self):
        """distinct metric SQL 에 SELECT DISTINCT 가 포함된다."""
        session, executed = _capturing_session()
        await analytics_search(
            session, group_by="min_class_name", metric="distinct"
        )
        assert "SELECT DISTINCT" in executed[0]

    async def test_distinct_order_by_column(self):
        """distinct metric 은 차원 컬럼으로 정렬한다."""
        session, executed = _capturing_session()
        await analytics_search(
            session, group_by="min_class_name", metric="distinct"
        )
        assert "ORDER BY min_class_name" in executed[0]
        assert "COUNT(*)" not in executed[0]

    async def test_distinct_returns_group_values(self):
        """distinct 결과는 group_value 만 가진다 (count 없음)."""
        rows = [{"group_value": "수영장"}, {"group_value": "테니스장"}]
        session = _make_session(rows)
        result = await analytics_search(
            session, group_by="min_class_name", metric="distinct"
        )
        assert result == rows

    async def test_distinct_empty_result_returns_empty_list(self):
        """distinct metric 도 결과 없으면 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await analytics_search(
            session, group_by="min_class_name", metric="distinct"
        )
        assert result == []


class TestAnalyticsSearchFilters:
    async def test_filters_in_bind(self):
        """필터 조합이 bind 파라미터로 전달된다."""
        session = _make_session([])
        await analytics_search(
            session,
            group_by="min_class_name",
            metric="count",
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
        )
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "체육시설"
        assert bind["area_name"] == "강남구"
        assert bind["service_status"] == "접수중"

    async def test_no_filters_bind_only_top_k(self):
        """필터 미적용 시 bind 에는 top_k 만 있고 필터 키는 없다."""
        session = _make_session([])
        await analytics_search(session, group_by="area_name", metric="count")
        bind = session.execute.call_args[0][1]
        assert set(bind.keys()) == {"top_k"}

    async def test_no_filters_where_has_no_filter_conditions(self):
        """필터 미적용 WHERE 에는 필터 조건이 없다 (정적 조건만)."""
        session, executed = _capturing_session()
        await analytics_search(session, group_by="area_name", metric="count")
        sql_text = executed[0]
        # 정적 조건만 존재.
        assert "deleted_at IS NULL" in sql_text
        assert "area_name IS NOT NULL" in sql_text
        # 필터 바인드 플레이스홀더는 등장하지 않는다.
        assert ":max_class_name" not in sql_text
        assert ":service_status" not in sql_text
        assert ":keyword" not in sql_text

    async def test_multi_filters_use_bind_placeholders_not_literals(self):
        """다중 필터 동시 적용 시 값은 bind 플레이스홀더로만 들어간다."""
        session, executed = _capturing_session()
        evil_area = "강남구' OR '1'='1"
        await analytics_search(
            session,
            group_by="min_class_name",
            metric="count",
            max_class_name="체육시설",
            area_name=evil_area,
            service_status="접수중",
        )
        sql_text = executed[0]
        # WHERE 절은 플레이스홀더만 사용 — 사용자 값 리터럴 미삽입.
        assert "max_class_name = :max_class_name" in sql_text
        assert "area_name = :area_name" in sql_text
        assert "service_status = :service_status" in sql_text
        assert evil_area not in sql_text
        assert "OR '1'='1" not in sql_text

    async def test_top_k_in_bind(self):
        """top_k 가 bind 로 전달된다."""
        session = _make_session([])
        await analytics_search(
            session, group_by="area_name", metric="count", top_k=5
        )
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == 5

    async def test_default_top_k_25(self):
        """top_k 기본값은 25 다."""
        session = _make_session([])
        await analytics_search(session, group_by="area_name", metric="count")
        bind = session.execute.call_args[0][1]
        assert bind["top_k"] == 25

    async def test_column_not_null_condition(self):
        """차원 컬럼 NOT NULL 조건이 항상 포함된다."""
        session, executed = _capturing_session()
        await analytics_search(session, group_by="area_name", metric="count")
        assert "area_name IS NOT NULL" in executed[0]
        assert "deleted_at IS NULL" in executed[0]


class TestAnalyticsSearchKeyword:
    async def test_keyword_escaped_and_wrapped(self):
        """keyword 는 sql_search 와 동일하게 이스케이프 후 %...% 래핑된다."""
        from tools.sql_search import _escape_like

        session = _make_session([])
        await analytics_search(
            session,
            group_by="area_name",
            metric="count",
            keyword="a%b_c\\d",
        )
        bind = session.execute.call_args[0][1]
        assert bind["keyword"] == f"%{_escape_like('a%b_c\\d')}%"

    async def test_keyword_uses_coalesce_concat_with_escape(self):
        """keyword 조건이 COALESCE 연결 표현식 + ESCAPE 절을 사용한다 (인덱스 일관성)."""
        session, executed = _capturing_session()
        await analytics_search(
            session, group_by="area_name", metric="count", keyword="테니스"
        )
        sql_text = executed[0]
        assert "COALESCE" in sql_text
        assert "ILIKE" in sql_text
        assert "ESCAPE" in sql_text

    async def test_keyword_injection_only_in_bind(self):
        """keyword 악성 값은 bind 로만 전달되고 SQL 문자열에 삽입되지 않는다."""
        session, executed = _capturing_session()
        malicious = "'; DROP TABLE public_service_reservations; --"
        await analytics_search(
            session, group_by="area_name", metric="count", keyword=malicious
        )
        assert malicious not in executed[0]
