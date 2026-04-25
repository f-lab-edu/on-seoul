"""scripts/embed_metadata.py 단위 테스트.

실제 DB 및 임베딩 API 없이 Mock으로 incremental 필터 로직을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_async_engine_mock() -> MagicMock:
    """dispose()가 awaitable한 AsyncEngine 모의 객체."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


def _make_session_factory_mock() -> MagicMock:
    """async with 컨텍스트 매니저를 지원하는 세션 팩토리 모의 객체."""
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


class TestIncrementalFilterLogic:
    """run() 함수의 incremental 필터 분기를 격리 테스트한다.

    DB 엔진 생성 및 실제 네트워크 호출 없이,
    _fetch_rows / _fetch_existing_service_ids / _upsert_batch 를 각각 mock하여
    rows 필터링 경로만 검증한다.
    """

    def _common_patches(
        self,
        all_rows: list[dict],
        existing_ids: set,
        upserted_ids: list[str],
        fetch_existing_mock: AsyncMock | None = None,
    ) -> tuple:
        """공통 patch 컨텍스트를 반환한다."""
        async def fake_upsert(session, rows, vectors):
            for r in rows:
                upserted_ids.append(r["service_id"])

        return (
            patch("scripts.embed_metadata.create_async_engine", return_value=_make_async_engine_mock()),
            patch("scripts.embed_metadata.async_sessionmaker", side_effect=lambda *a, **kw: _make_session_factory_mock()),
            patch("scripts.embed_metadata._fetch_rows", new=AsyncMock(return_value=all_rows)),
            patch(
                "scripts.embed_metadata._fetch_existing_service_ids",
                new=fetch_existing_mock or AsyncMock(return_value=existing_ids),
            ),
            patch("scripts.embed_metadata._upsert_batch", new=AsyncMock(side_effect=fake_upsert)),
            patch(
                "scripts.embed_metadata.get_embeddings",
                return_value=MagicMock(
                    aembed_documents=AsyncMock(side_effect=lambda docs: [[0.1] * 3 for _ in docs])
                ),
            ),
        )

    async def test_incremental_empty_existing_ids_processes_all_rows(self):
        """existing_ids가 비어 있으면(첫 실행) fetch한 모든 행을 임베딩한다."""
        all_rows = [_make_row("S001"), _make_row("S002"), _make_row("S003")]
        upserted_service_ids: list[str] = []
        patches = self._common_patches(all_rows, set(), upserted_service_ids)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from scripts.embed_metadata import run
            await run(limit=None, incremental=True)

        assert sorted(upserted_service_ids) == ["S001", "S002", "S003"]

    async def test_incremental_all_existing_processes_zero_rows(self):
        """existing_ids가 전체 데이터와 동일하면 0건을 처리하고 종료한다."""
        all_rows = [_make_row("S001"), _make_row("S002")]
        existing_ids = {"S001", "S002"}
        upserted_service_ids: list[str] = []
        patches = self._common_patches(all_rows, existing_ids, upserted_service_ids)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from scripts.embed_metadata import run
            await run(limit=None, incremental=True)

        assert upserted_service_ids == [], "전부 기존 데이터이므로 upsert 호출이 없어야 함"

    async def test_incremental_partial_existing_processes_only_new_rows(self):
        """existing_ids가 일부이면 새로운 service_id만 임베딩한다."""
        all_rows = [_make_row("S001"), _make_row("S002"), _make_row("S003")]
        existing_ids = {"S001"}  # S002, S003만 신규
        upserted_service_ids: list[str] = []
        patches = self._common_patches(all_rows, existing_ids, upserted_service_ids)

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from scripts.embed_metadata import run
            await run(limit=None, incremental=True)

        assert sorted(upserted_service_ids) == ["S002", "S003"]

    async def test_non_incremental_processes_all_rows_regardless_of_existing(self):
        """incremental=False이면 existing_ids 조회 없이 전체 행을 처리한다."""
        all_rows = [_make_row("S001"), _make_row("S002")]
        upserted_service_ids: list[str] = []
        mock_fetch_existing = AsyncMock(return_value={"S001", "S002"})
        patches = self._common_patches(
            all_rows, set(), upserted_service_ids, fetch_existing_mock=mock_fetch_existing
        )

        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            from scripts.embed_metadata import run
            await run(limit=None, incremental=False)

        # incremental=False이므로 _fetch_existing_service_ids 호출 없어야 함
        mock_fetch_existing.assert_not_called()
        assert sorted(upserted_service_ids) == ["S001", "S002"]

    async def test_incremental_fetch_rows_returns_empty_exits_early(self):
        """fetch_rows가 빈 리스트를 반환하면 _fetch_existing_service_ids 호출 없이 종료한다."""
        mock_fetch_existing = AsyncMock(return_value=set())
        mock_upsert = AsyncMock()

        with (
            patch("scripts.embed_metadata.create_async_engine", return_value=_make_async_engine_mock()),
            patch("scripts.embed_metadata.async_sessionmaker", side_effect=lambda *a, **kw: _make_session_factory_mock()),
            patch("scripts.embed_metadata._fetch_rows", new=AsyncMock(return_value=[])),
            patch("scripts.embed_metadata._fetch_existing_service_ids", new=mock_fetch_existing),
            patch("scripts.embed_metadata._upsert_batch", new=mock_upsert),
            patch("scripts.embed_metadata.get_embeddings", return_value=MagicMock()),
        ):
            from scripts.embed_metadata import run
            await run(limit=None, incremental=True)

        mock_fetch_existing.assert_not_called()
        mock_upsert.assert_not_called()


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
