"""tools/hydrate_services.py 단위 테스트.

Mock AsyncSession으로 입력 검증, bind 파라미터, 반환 순서를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: F401

from tools.hydrate_services import hydrate_services


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession — execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = []
        mock_result.fetchall.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session


class TestHydrateEmptyInput:
    async def test_empty_service_ids_returns_empty_list(self):
        """service_id 리스트가 비어 있으면 DB 호출 없이 빈 리스트 반환."""
        session = _make_session([])
        result = await hydrate_services(session, [])
        assert result == []
        session.execute.assert_not_called()


class TestHydrateOrderPreservation:
    async def test_returns_in_input_order(self):
        """결과는 service_ids 입력 순서(=검색 순위)를 유지한다."""
        # DB는 임의 순서로 반환 — 정렬은 도구 책임
        db_rows = [
            {"service_id": "S002", "service_name": "수영장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "강남구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
            {"service_id": "S001", "service_name": "테니스장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "마포구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
        ]
        session = _make_session(db_rows)
        result = await hydrate_services(session, ["S001", "S002"])
        assert [r["service_id"] for r in result] == ["S001", "S002"]


class TestHydrateMissingRows:
    async def test_missing_service_ids_excluded(self):
        """원본 테이블에 없거나 soft-delete된 service_id는 결과에서 제외된다."""
        db_rows = [
            {"service_id": "S001", "service_name": "테니스장", "max_class_name": "체육시설",
             "min_class_name": None, "area_name": "마포구", "place_name": None,
             "service_status": "접수중", "payment_type": None, "service_url": None,
             "receipt_start_dt": None, "receipt_end_dt": None,
             "service_open_start_dt": None, "service_open_end_dt": None,
             "coord_x": None, "coord_y": None, "target_info": None},
        ]
        session = _make_session(db_rows)
        # S002는 임베딩엔 있지만 원본엔 없는 케이스
        result = await hydrate_services(session, ["S001", "S002", "S003"])
        assert len(result) == 1
        assert result[0]["service_id"] == "S001"


class TestHydrateSqlSafety:
    async def test_service_ids_passed_as_bind_param(self):
        """service_id 값은 bind 파라미터로 전달되고 SQL 템플릿에 직접 삽입되지 않는다."""
        malicious = "'; DROP TABLE public_service_reservations; --"
        session = _make_session([])
        await hydrate_services(session, [malicious])

        stmt, params = session.execute.call_args[0][0], session.execute.call_args[0][1]
        # bind 파라미터로 전달됨
        assert params["service_ids"] == [malicious]
        # SQL 템플릿 문자열에는 삽입되지 않음
        assert malicious not in str(stmt)

    async def test_deleted_at_filter_in_sql(self):
        """soft-delete 필터(deleted_at IS NULL)가 SQL에 포함된다."""
        session = _make_session([])
        await hydrate_services(session, ["S001"])
        stmt = session.execute.call_args[0][0]
        assert "deleted_at IS NULL" in str(stmt)
