"""tools/sql_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB에 접근하지 않는다.
"""

from datetime import date
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

    async def test_area_name_single_uses_any(self):
        """area_name 단일 지역도 areas 리스트 bind + ANY 술어로 매칭한다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        await sql_search(session, area_name=["마포구"])
        bind = session.execute.call_args[0][1]
        assert bind["areas"] == ["마포구"]
        assert "area_name = ANY(:areas)" in executed_sqls[0]

    async def test_area_name_multi_region_any(self):
        """다중 지역이 areas 리스트로 전달되어 ANY 로 OR 매칭된다."""
        session = _make_session([])
        await sql_search(session, area_name=["성동구", "광진구"])
        bind = session.execute.call_args[0][1]
        assert bind["areas"] == ["성동구", "광진구"]

    async def test_scalar_area_not_char_split(self):
        """스칼라 str 이 새어들어와도 areas bind 가 ['성','동','구']로 쪼개지지 않는다."""
        session = _make_session([])
        # 타입 계약상 list 지만 상류 오주입 방어 가드를 검증한다.
        await sql_search(session, area_name="성동구")  # type: ignore[arg-type]
        bind = session.execute.call_args[0][1]
        assert bind["areas"] == ["성동구"]

    async def test_empty_area_list_omits_condition(self):
        """area_name=[] 는 필터 미적용(areas bind 없음)."""
        session = _make_session([])
        await sql_search(session, area_name=[])
        bind = session.execute.call_args[0][1]
        assert "areas" not in bind

    async def test_target_audience_or_like_parameterized(self):
        """target_audience 는 토큰맵 OR-LIKE 술어 + bind 로 적용된다(값 삽입 금지)."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        await sql_search(session, target_audience="CHILD")
        bind = session.execute.call_args[0][1]
        assert "target_info LIKE :aud_0" in executed_sqls[0]
        assert "%초등학생%" in bind.values()
        assert "%제한없음%" in bind.values()

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
        assert "areas" not in bind
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


class TestSqlSearchPaymentFilter:
    async def test_free_exact_match_bind(self):
        """payment_type="무료" → bind 값 '무료' (정확 매칭)."""
        session = _make_session([])
        await sql_search(session, payment_type="무료")
        bind = session.execute.call_args[0][1]
        assert bind["payment_type"] == "무료"

    async def test_free_exact_match_sql_text(self):
        """payment_type="무료" → '=' 정확 매칭 조건."""
        executed: list[str] = []

        async def _capture(stmt, params=None):
            executed.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        await sql_search(session, payment_type="무료")
        assert "payment_type = :payment_type" in executed[0]
        assert "LIKE" not in executed[0]

    async def test_paid_prefix_match_bind(self):
        """payment_type="유료" → bind 값 '유료%' (접두 매칭)."""
        session = _make_session([])
        await sql_search(session, payment_type="유료")
        bind = session.execute.call_args[0][1]
        assert bind["payment_type"] == "유료%"

    async def test_paid_prefix_match_sql_text(self):
        """payment_type="유료" → LIKE 접두 매칭 + ESCAPE."""
        executed: list[str] = []

        async def _capture(stmt, params=None):
            executed.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        await sql_search(session, payment_type="유료")
        assert "payment_type LIKE :payment_type ESCAPE" in executed[0]

    async def test_none_payment_no_condition(self):
        """payment_type=None → 조건/bind 미포함."""
        executed: list[str] = []

        async def _capture(stmt, params=None):
            executed.append(str(stmt))
            m = MagicMock()
            m.keys.return_value = []
            m.fetchall.return_value = []
            return m

        session = MagicMock()
        session.execute = AsyncMock(side_effect=_capture)
        await sql_search(session)
        bind = session.execute.call_args[0][1]
        assert "payment_type" not in bind
        # payment_type 은 SELECT 컬럼이므로 WHERE 조건(바인드 참조)이 없는지로 검증한다.
        assert ":payment_type" not in executed[0]


class TestSqlSearchAllFilters:
    async def test_all_filters_combined_in_bind(self):
        """모든 필터를 동시에 전달하면 bind에 모두 포함된다."""
        session = _make_session([])
        await sql_search(
            session,
            max_class_name="체육시설",
            area_name=["강남구"],
            service_status="접수중",
            keyword="수영",
        )
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "체육시설"
        assert bind["areas"] == ["강남구"]
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
            area_name=["강남구"],
            service_status="접수중",
            keyword="수영",
        )

        sql_text = executed_sqls[0]
        assert "max_class_name" in sql_text
        assert "area_name" in sql_text
        assert "service_status" in sql_text
        assert "ILIKE" in sql_text


class TestSqlSearchDateFilters:
    async def test_receipt_date_from_in_bind(self):
        """receipt_date_from이 있으면 bind에 포함된다."""
        session = _make_session([])
        d = date(2026, 5, 18)
        await sql_search(session, receipt_date_from=d)
        bind = session.execute.call_args[0][1]
        assert bind["receipt_date_from"] == d

    async def test_receipt_date_to_in_bind(self):
        """receipt_date_to가 있으면 bind에 포함된다."""
        session = _make_session([])
        d = date(2026, 5, 24)
        await sql_search(session, receipt_date_to=d)
        bind = session.execute.call_args[0][1]
        assert bind["receipt_date_to"] == d

    async def test_receipt_date_overlap_conditions_in_sql(self):
        """날짜 필터가 구간 겹침 조건(receipt_end_dt >=, receipt_start_dt <=)으로 생성된다."""
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
            receipt_date_from=date(2026, 5, 18),
            receipt_date_to=date(2026, 5, 24),
        )

        sql_text = executed_sqls[0]
        assert "receipt_end_dt >= :receipt_date_from" in sql_text
        assert "receipt_start_dt <= :receipt_date_to" in sql_text

    async def test_no_date_filter_excludes_date_keys(self):
        """날짜 파라미터 없이 호출하면 bind에 날짜 키가 없다."""
        session = _make_session([])
        await sql_search(session)
        bind = session.execute.call_args[0][1]
        assert "receipt_date_from" not in bind
        assert "receipt_date_to" not in bind

    async def test_date_filters_combined_with_other_filters(self):
        """날짜 필터와 다른 필터를 동시에 사용할 수 있다."""
        session = _make_session([])
        await sql_search(
            session,
            max_class_name="문화행사",
            area_name=["마포구"],
            receipt_date_from=date(2026, 5, 1),
            receipt_date_to=date(2026, 5, 31),
        )
        bind = session.execute.call_args[0][1]
        assert bind["max_class_name"] == "문화행사"
        assert bind["areas"] == ["마포구"]
        assert bind["receipt_date_from"] == date(2026, 5, 1)
        assert bind["receipt_date_to"] == date(2026, 5, 31)


class TestSqlSearchOrderStability:
    """receipt_start_dt 동률 시 service_id ASC 2차 정렬로 결정적 순서 보장."""

    async def test_order_by_includes_service_id_tiebreaker(self):
        """ORDER BY 절에 service_id ASC 2차 정렬 키가 포함된다."""
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

        sql_text = executed_sqls[0]
        assert "receipt_start_dt DESC NULLS LAST" in sql_text
        # 동률 그룹 내부를 결정적으로 정렬하는 2차 키
        assert "service_id ASC" in sql_text
        # 최신순 우선(1차 키) → service_id(2차 키) 순서를 보장
        assert sql_text.index("receipt_start_dt DESC") < sql_text.index(
            "service_id ASC"
        )

    async def test_primary_sort_intent_preserved(self):
        """1차 정렬 의도(최신순 DESC)가 service_id 추가로 깨지지 않는다."""
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

        order_clause = executed_sqls[0].split("ORDER BY", 1)[1]
        # 1차 키는 여전히 receipt_start_dt DESC (날짜 경계에서만 날짜가 바뀜)
        assert order_clause.strip().startswith("receipt_start_dt DESC")
        # service_id 는 ASC (동률 그룹 내부에서만 순서 결정)
        assert "service_id DESC" not in order_clause

    async def test_service_id_is_strictly_secondary_key(self):
        """service_id 는 정확히 2차 키 — 1차 키 뒤 첫 번째이자 유일한 콤마 키.

        2차 키가 실수로 1차로 승격되면 최신순 의도가 깨진다. ORDER BY 절을
        콤마로 분해해 [primary, secondary] 정확히 2개이며 순서가 맞는지 단언한다.
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

        await sql_search(session)

        # "ORDER BY <clause> LIMIT ..." 에서 clause만 추출
        order_clause = executed_sqls[0].split("ORDER BY", 1)[1].split("LIMIT", 1)[0]
        keys = [k.strip() for k in order_clause.split(",")]
        assert len(keys) == 2, f"정렬 키는 정확히 2개여야 한다: {keys}"
        assert keys[0] == "receipt_start_dt DESC NULLS LAST"
        assert keys[1] == "service_id ASC"

    async def test_tied_rows_returned_in_service_id_order(self):
        """동률(receipt_start_dt 동일) 입력에서 반환 순서가 보존된다(값 수준).

        DB가 (receipt_start_dt DESC, service_id ASC)로 정렬해 넘긴 행 순서를
        sql_search 가 재배열·드롭 없이 그대로 반환하는지 단언한다. DB 정렬 보장
        자체는 SQL 문자열 + 실DB 검증의 몫이며, 본 테스트는 도구의 순서 보존
        (passthrough)을 고정한다.
        """
        # 동일 receipt_start_dt, service_id 오름차순으로 이미 정렬된 DB 결과를 모사
        tied_rows = [
            {
                "service_id": "S001",
                "service_name": "가 시설",
                "receipt_start_dt": date(2026, 6, 1),
            },
            {
                "service_id": "S002",
                "service_name": "나 시설",
                "receipt_start_dt": date(2026, 6, 1),
            },
            {
                "service_id": "S003",
                "service_name": "다 시설",
                "receipt_start_dt": date(2026, 6, 1),
            },
        ]
        session = _make_session(tied_rows)

        result = await sql_search(session)

        assert [r["service_id"] for r in result] == ["S001", "S002", "S003"]
        # 모든 행이 동률(같은 receipt_start_dt)임을 명시 — 순서를 가르는 건 service_id뿐
        assert {r["receipt_start_dt"] for r in result} == {date(2026, 6, 1)}


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
