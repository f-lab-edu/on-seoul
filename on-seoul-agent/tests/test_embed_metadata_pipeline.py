"""scripts/embed_metadata.py 오케스트레이터 단위 테스트.

process_service 함수의 트랙 조건 분기와 호출 순서를 검증한다.
트랙 모듈(identity/summary/questions)과 extract_metadata를 patch하여
실제 DB/LLM 없이 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch


from llm.extractor import ExtractedMetadata
from scripts.embed_metadata import process_service
from scripts.tracks._shared import ServiceRecord


def _make_service(service_id: str = "S001") -> ServiceRecord:
    return {
        "service_id": service_id,
        "service_name": f"시설 {service_id}",
        "area_name": "강남구",
        "max_class_name": "체육시설",
        "min_class_name": "헬스장",
        "place_name": "강남헬스",
        "target_info": "성인",
        "payment_type": "무료",
        "detail_content": "3. 상세내용\n자세한 내용\n4. 주의사항\n주의 사항",
        "service_status": "접수중",
        "service_url": None,
        "service_gubun": "체육",
        "receipt_start_dt": None,
        "receipt_end_dt": None,
        "service_open_start_dt": None,
        "service_open_end_dt": None,
        "coord_x": None,
        "coord_y": None,
    }


def _make_session():
    session = MagicMock()
    session.execute = AsyncMock()

    # begin() context manager
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=cm)
    return session


def _make_extracted() -> ExtractedMetadata:
    return ExtractedMetadata(summary="강남 헬스장", fee="무료")


class TestProcessServiceAllTracks:
    async def test_all_tracks_called_when_extraction_succeeds(self):
        """extraction 성공 시 A/B/C 트랙이 모두 호출된다."""
        service = _make_service()
        session = _make_session()
        extracted = _make_extracted()

        with (
            patch("scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()),
            patch("scripts.embed_metadata.extract_metadata", AsyncMock(return_value=extracted)),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()) as mock_a,
            patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()) as mock_b,
            patch("scripts.embed_metadata.embed_and_insert_questions", AsyncMock(return_value=True)) as mock_c,
        ):
            await process_service(
                service,
                session=session,
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
            )

        mock_a.assert_called_once()
        mock_b.assert_called_once()
        mock_c.assert_called_once()

    async def test_track_b_c_skipped_when_extraction_fails(self):
        """extraction 실패(None 반환) 시 B/C 트랙은 호출되지 않는다."""
        service = _make_service()
        session = _make_session()

        with (
            patch("scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()),
            patch("scripts.embed_metadata.extract_metadata", AsyncMock(return_value=None)),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()) as mock_a,
            patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()) as mock_b,
            patch("scripts.embed_metadata.embed_and_insert_questions", AsyncMock()) as mock_c,
        ):
            await process_service(
                service,
                session=session,
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
            )

        mock_a.assert_called_once()
        mock_b.assert_not_called()
        mock_c.assert_not_called()

    async def test_extraction_failure_writes_to_failed_path(self, tmp_path):
        """extraction 실패 시 extraction_failed_path에 service_id가 기록된다."""
        service = _make_service("FAIL_ID")
        session = _make_session()
        failed_path = tmp_path / "extraction_failed.tsv"

        with (
            patch("scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()),
            patch("scripts.embed_metadata.extract_metadata", AsyncMock(return_value=None)),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()),
        ):
            await process_service(
                service,
                session=session,
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
                extraction_failed_path=failed_path,
            )

        assert failed_path.read_text().strip() == "FAIL_ID"


class TestProcessServiceTrackA:
    async def test_only_track_a_called_when_tracks_is_a(self):
        """tracks={'A'}이면 B/C 트랙이 호출되지 않는다."""
        service = _make_service()
        session = _make_session()

        with (
            patch("scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()),
            patch("scripts.embed_metadata.extract_metadata", AsyncMock(return_value=_make_extracted())),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()) as mock_a,
            patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()) as mock_b,
            patch("scripts.embed_metadata.embed_and_insert_questions", AsyncMock()) as mock_c,
        ):
            await process_service(
                service,
                session=session,
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A"},
            )

        mock_a.assert_called_once()
        mock_b.assert_not_called()
        mock_c.assert_not_called()


class TestProcessServiceDeleteCalled:
    async def test_delete_called_with_correct_tracks(self):
        """delete_rows_by_service_id가 service_id와 tracks로 호출된다."""
        service = _make_service("DEL_ID")
        session = _make_session()

        with (
            patch("scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()) as mock_del,
            patch("scripts.embed_metadata.extract_metadata", AsyncMock(return_value=_make_extracted())),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()),
            patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()),
            patch("scripts.embed_metadata.embed_and_insert_questions", AsyncMock(return_value=True)),
        ):
            await process_service(
                service,
                session=session,
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B"},
            )

        mock_del.assert_called_once()
        call_kwargs = mock_del.call_args[1]
        assert call_kwargs["tracks"] == {"A", "B"}
