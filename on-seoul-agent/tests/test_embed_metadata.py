"""scripts/embed_metadata.py 단위 테스트.

실제 DB 및 임베딩 API 없이 Mock으로 incremental 필터 로직을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_async_engine_mock() -> MagicMock:
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


def _make_session_factory_mock() -> MagicMock:
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    mock_factory = MagicMock(return_value=mock_session)
    return mock_factory


def _make_row(service_id: str) -> dict:
    return {
        "service_id": service_id,
        "service_name": f"서비스 {service_id}",
        "service_gubun": "체육",
        "max_class_name": "체육시설",
        "min_class_name": "헬스장",
        "area_name": "강남구",
        "place_name": "강남헬스",
        "service_status": "접수중",
        "payment_type": "무료",
        "target_info": "성인",
        "service_url": None,
        "detail_content": "상세 내용",
        "receipt_start_dt": None,
        "receipt_end_dt": None,
        "service_open_start_dt": None,
        "service_open_end_dt": None,
        "coord_x": None,
        "coord_y": None,
    }


def _common_patches(
    all_rows: list[dict],
    existing_ids: set,
    processed_ids: list[str],
    fetch_existing_mock: AsyncMock | None = None,
) -> tuple:
    """공통 patch 컨텍스트. process_service를 mock하여 service_id를 기록한다."""

    async def fake_process_service(service, **kwargs):
        processed_ids.append(service["service_id"])

    return (
        patch(
            "scripts.embed_metadata.create_async_engine",
            return_value=_make_async_engine_mock(),
        ),
        patch(
            "scripts.embed_metadata.async_sessionmaker",
            side_effect=lambda *a, **kw: _make_session_factory_mock(),
        ),
        patch(
            "scripts.embed_metadata._fetch_rows", new=AsyncMock(return_value=all_rows)
        ),
        patch(
            "scripts.embed_metadata._fetch_existing_service_ids",
            new=fetch_existing_mock or AsyncMock(return_value=existing_ids),
        ),
        patch(
            "scripts.embed_metadata.process_service",
            new=AsyncMock(side_effect=fake_process_service),
        ),
        patch("scripts.embed_metadata.get_embeddings", return_value=MagicMock()),
        patch("scripts.embed_metadata.get_chat_model", return_value=MagicMock()),
    )


class TestIncrementalFilterLogic:
    """run() 함수의 incremental 필터 분기를 격리 테스트한다."""

    async def test_incremental_empty_existing_ids_processes_all_rows(self):
        """existing_ids가 비어 있으면(첫 실행) fetch한 모든 행을 처리한다."""
        all_rows = [_make_row("S001"), _make_row("S002"), _make_row("S003")]
        processed: list[str] = []
        patches = _common_patches(all_rows, set(), processed)

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            from scripts.embed_metadata import run

            await run(limit=None, incremental=True)

        assert sorted(processed) == ["S001", "S002", "S003"]

    async def test_incremental_all_existing_processes_zero_rows(self):
        """existing_ids가 전체 데이터와 동일하면 0건을 처리하고 종료한다."""
        all_rows = [_make_row("S001"), _make_row("S002")]
        existing_ids = {"S001", "S002"}
        processed: list[str] = []
        patches = _common_patches(all_rows, existing_ids, processed)

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            from scripts.embed_metadata import run

            await run(limit=None, incremental=True)

        assert processed == []

    async def test_incremental_partial_existing_processes_only_new_rows(self):
        """existing_ids가 일부이면 새로운 service_id만 처리한다."""
        all_rows = [_make_row("S001"), _make_row("S002"), _make_row("S003")]
        existing_ids = {"S001"}
        processed: list[str] = []
        patches = _common_patches(all_rows, existing_ids, processed)

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            from scripts.embed_metadata import run

            await run(limit=None, incremental=True)

        assert sorted(processed) == ["S002", "S003"]

    async def test_non_incremental_processes_all_rows_regardless_of_existing(self):
        """incremental=False이면 existing_ids 조회 없이 전체 행을 처리한다."""
        all_rows = [_make_row("S001"), _make_row("S002")]
        processed: list[str] = []
        mock_fetch_existing = AsyncMock(return_value={"S001", "S002"})
        patches = _common_patches(
            all_rows, set(), processed, fetch_existing_mock=mock_fetch_existing
        )

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
        ):
            from scripts.embed_metadata import run

            await run(limit=None, incremental=False)

        mock_fetch_existing.assert_not_called()
        assert sorted(processed) == ["S001", "S002"]

    async def test_incremental_fetch_rows_returns_empty_exits_early(self):
        """fetch_rows가 빈 리스트를 반환하면 _fetch_existing_service_ids 호출 없이 종료한다."""
        mock_fetch_existing = AsyncMock(return_value=set())
        mock_process = AsyncMock()

        with (
            patch(
                "scripts.embed_metadata.create_async_engine",
                return_value=_make_async_engine_mock(),
            ),
            patch(
                "scripts.embed_metadata.async_sessionmaker",
                side_effect=lambda *a, **kw: _make_session_factory_mock(),
            ),
            patch("scripts.embed_metadata._fetch_rows", new=AsyncMock(return_value=[])),
            patch(
                "scripts.embed_metadata._fetch_existing_service_ids",
                new=mock_fetch_existing,
            ),
            patch("scripts.embed_metadata.process_service", new=mock_process),
            patch("scripts.embed_metadata.get_embeddings", return_value=MagicMock()),
            patch("scripts.embed_metadata.get_chat_model", return_value=MagicMock()),
        ):
            from scripts.embed_metadata import run

            await run(limit=None, incremental=True)

        mock_fetch_existing.assert_not_called()
        mock_process.assert_not_called()


class TestFetchExistingServiceIds:
    """_fetch_existing_service_ids 단위 테스트."""

    async def test_returns_set_of_service_ids(self):
        """DB 결과에서 service_id set을 반환한다."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("S001",), ("S002",), ("S003",)]
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        from scripts.embed_metadata import _fetch_existing_service_ids

        result = await _fetch_existing_service_ids(mock_session)

        assert result == {"S001", "S002", "S003"}

    async def test_returns_empty_set_when_no_rows(self):
        """DB가 비어 있으면 빈 set을 반환한다."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        from scripts.embed_metadata import _fetch_existing_service_ids

        result = await _fetch_existing_service_ids(mock_session)

        assert result == set()

    async def test_track_b_filters_by_summary_row_kind(self):
        """tracks={"B"} 이면 row_kind='summary' 조건이 SQL + bind에 포함된다."""
        executed: list[tuple] = []  # (sql_str, params)

        async def _capture(stmt, params=None):
            executed.append((str(stmt), params or {}))
            m = MagicMock()
            m.fetchall.return_value = []
            return m

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(side_effect=_capture)

        from scripts.embed_metadata import _fetch_existing_service_ids

        await _fetch_existing_service_ids(mock_session, tracks={"B"})

        assert executed, "execute가 호출되어야 한다"
        sql_str, bind = executed[0]
        assert "row_kind" in sql_str
        assert "summary" in bind.values()

    async def test_track_a_filters_by_identity_row_kind(self):
        """tracks={"A"} 이면 row_kind='identity' 조건이 SQL + bind에 포함된다."""
        executed: list[tuple] = []

        async def _capture(stmt, params=None):
            executed.append((str(stmt), params or {}))
            m = MagicMock()
            m.fetchall.return_value = []
            return m

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(side_effect=_capture)

        from scripts.embed_metadata import _fetch_existing_service_ids

        await _fetch_existing_service_ids(mock_session, tracks={"A"})

        sql_str, bind = executed[0]
        assert "row_kind" in sql_str
        assert "identity" in bind.values()

    async def test_all_tracks_no_row_kind_filter(self):
        """tracks가 A/B/C 전체이면 row_kind 필터 없이 전체 조회한다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.fetchall.return_value = []
            return m

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(side_effect=_capture)

        from scripts.embed_metadata import _fetch_existing_service_ids

        await _fetch_existing_service_ids(mock_session, tracks={"A", "B", "C"})

        assert "row_kind" not in executed_sqls[0]

    async def test_tracks_none_no_row_kind_filter(self):
        """tracks=None 이면 row_kind 필터 없이 전체 조회한다."""
        executed_sqls: list[str] = []

        async def _capture(stmt, params=None):
            executed_sqls.append(str(stmt))
            m = MagicMock()
            m.fetchall.return_value = []
            return m

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(side_effect=_capture)

        from scripts.embed_metadata import _fetch_existing_service_ids

        await _fetch_existing_service_ids(mock_session, tracks=None)

        assert "row_kind" not in executed_sqls[0]
