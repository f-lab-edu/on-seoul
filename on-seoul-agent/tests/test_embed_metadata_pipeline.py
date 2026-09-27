"""scripts/embed_metadata.py 오케스트레이터 단위 테스트.

process_service 함수의 트랙 조건 분기와 호출 순서를 검증한다.
트랙 모듈(identity/summary/questions)과 extract_metadata를 patch하여
실제 DB/LLM 없이 검증한다.
"""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch


from sqlalchemy.exc import InvalidRequestError

from llm.extractor import ExtractedMetadata
from scripts.cleaning.detail_content import clean_detail_content
from scripts.embed_metadata import process_service
from scripts.tracks._shared import ServiceRecord, compute_source_hash


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


def _make_session(*, source_hash: str | None = None):
    """SQLAlchemy 2.0 의 autobegin 의미를 흉내내는 가짜 세션.

    단순 MagicMock 은 `execute()` 후 `begin()` 을 열어도 통과시키지만, 실제
    SQLAlchemy 2.0 은 execute() 시점에 트랜잭션을 autobegin 하므로 그 뒤의
    session.begin() 이 InvalidRequestError 로 터진다. 이 차이를 mock 이 삼키면
    "운영에서는 전 건 실패하는데 테스트는 green" 인 상태가 만들어진다
    (실제로 한 번 발생했다). 그래서 여기서 트랜잭션 상태를 추적해 재현한다.
    """
    session = MagicMock()
    state = {"in_tx": False}

    result = MagicMock()
    result.fetchone = MagicMock(
        return_value=None if source_hash is None else (source_hash,)
    )

    async def _execute(*_args, **_kwargs):
        # 실제 Session.execute 와 동일하게 트랜잭션을 autobegin 한다.
        state["in_tx"] = True
        return result

    session.execute = AsyncMock(side_effect=_execute)

    def _begin():
        if state["in_tx"]:
            raise InvalidRequestError(
                "A transaction is already begun on this Session."
            )
        cm = MagicMock()

        async def _aenter():
            state["in_tx"] = True

        async def _aexit(*_exc):
            state["in_tx"] = False  # 커밋/롤백으로 트랜잭션 해제
            return False

        cm.__aenter__ = AsyncMock(side_effect=_aenter)
        cm.__aexit__ = AsyncMock(side_effect=_aexit)
        return cm

    session.begin = MagicMock(side_effect=_begin)
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
            patch(
                "scripts.embed_metadata.extract_metadata",
                AsyncMock(return_value=extracted),
            ),
            patch(
                "scripts.embed_metadata.embed_and_insert_identity", AsyncMock()
            ) as mock_a,
            patch(
                "scripts.embed_metadata.embed_and_insert_summary", AsyncMock()
            ) as mock_b,
            patch(
                "scripts.embed_metadata.embed_and_insert_questions",
                AsyncMock(return_value=True),
            ) as mock_c,
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
            patch(
                "scripts.embed_metadata.extract_metadata", AsyncMock(return_value=None)
            ),
            patch(
                "scripts.embed_metadata.embed_and_insert_identity", AsyncMock()
            ) as mock_a,
            patch(
                "scripts.embed_metadata.embed_and_insert_summary", AsyncMock()
            ) as mock_b,
            patch(
                "scripts.embed_metadata.embed_and_insert_questions", AsyncMock()
            ) as mock_c,
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
            patch(
                "scripts.embed_metadata.extract_metadata", AsyncMock(return_value=None)
            ),
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
            patch(
                "scripts.embed_metadata.extract_metadata",
                AsyncMock(return_value=_make_extracted()),
            ),
            patch(
                "scripts.embed_metadata.embed_and_insert_identity", AsyncMock()
            ) as mock_a,
            patch(
                "scripts.embed_metadata.embed_and_insert_summary", AsyncMock()
            ) as mock_b,
            patch(
                "scripts.embed_metadata.embed_and_insert_questions", AsyncMock()
            ) as mock_c,
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
            patch(
                "scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()
            ) as mock_del,
            patch(
                "scripts.embed_metadata.extract_metadata",
                AsyncMock(return_value=_make_extracted()),
            ),
            patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()),
            patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()),
            patch(
                "scripts.embed_metadata.embed_and_insert_questions",
                AsyncMock(return_value=True),
            ),
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


class TestSourceHash:
    def test_none_and_empty_string_hash_equally(self):
        """None과 빈 문자열/공백은 같은 해시를 만든다."""
        a = _make_service()
        a["target_info"] = None
        b = _make_service()
        b["target_info"] = "  "

        assert compute_source_hash(a, "") == compute_source_hash(b, "")

    def test_field_boundary_is_unambiguous(self):
        """필드 경계 이동은 다른 해시를 만든다."""
        a = _make_service()
        a["area_name"], a["max_class_name"] = "강남", "구체육시설"
        b = _make_service()
        b["area_name"], b["max_class_name"] = "강남구", "체육시설"

        assert compute_source_hash(a, "") != compute_source_hash(b, "")

    def test_volatile_fields_do_not_affect_hash(self):
        """service_status/receipt_*_dt 는 해시에 영향을 주지 않는다."""
        a = _make_service()
        b = _make_service()
        b["service_status"] = "접수마감"
        b["receipt_end_dt"] = "2026-01-01"
        b["coord_x"] = 127.0

        assert compute_source_hash(a, "x") == compute_source_hash(b, "x")

    def test_cleaned_detail_affects_hash(self):
        """정제된 상세내용이 바뀌면 해시가 바뀐다."""
        service = _make_service()

        assert compute_source_hash(service, "내용 A") != compute_source_hash(
            service, "내용 B"
        )


class _SkipPatches:
    """스킵 경로 검증용 patch 묶음 (ExitStack 으로 일괄 적용)."""

    def __init__(self, *, stored_hash: str | None):
        self._patches = {
            "fetch": patch(
                "scripts.embed_metadata.fetch_source_hash",
                AsyncMock(return_value=stored_hash),
            ),
            "update": patch(
                "scripts.embed_metadata.update_identity_metadata", AsyncMock()
            ),
            "delete": patch(
                "scripts.embed_metadata.delete_rows_by_service_id", AsyncMock()
            ),
            "extract": patch(
                "scripts.embed_metadata.extract_metadata",
                AsyncMock(return_value=_make_extracted()),
            ),
            "a": patch("scripts.embed_metadata.embed_and_insert_identity", AsyncMock()),
            "b": patch("scripts.embed_metadata.embed_and_insert_summary", AsyncMock()),
            "c": patch(
                "scripts.embed_metadata.embed_and_insert_questions",
                AsyncMock(return_value=True),
            ),
        }
        self.mocks: dict = {}

    def __enter__(self):
        self._stack = ExitStack()
        for name, p in self._patches.items():
            self.mocks[name] = self._stack.enter_context(p)
        return self.mocks

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


class TestProcessServiceSourceHashSkip:
    async def test_unchanged_source_skips_llm_and_refreshes_metadata(self):
        """소스 필드 불변 + 상태/날짜만 변경 → LLM 미호출, metadata만 갱신."""
        service = _make_service()
        stored = compute_source_hash(
            service, clean_detail_content(service["detail_content"])
        )
        service["service_status"] = "접수마감"
        service["receipt_end_dt"] = "2026-03-01"

        with _SkipPatches(stored_hash=stored) as m:
            result = await process_service(
                service,
                session=_make_session(),
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
            )

        assert result == "skipped"
        m["extract"].assert_not_called()
        m["a"].assert_not_called()
        m["b"].assert_not_called()
        m["c"].assert_not_called()
        m["delete"].assert_not_called()
        m["update"].assert_called_once()
        assert m["update"].call_args[1]["source_hash"] == stored
        assert m["update"].call_args[0][1]["service_status"] == "접수마감"

    async def test_changed_source_regenerates_and_stores_new_hash(self):
        """소스 필드 변경 → 전량 재생성 + 새 해시 저장."""
        service = _make_service()
        expected = compute_source_hash(
            service, clean_detail_content(service["detail_content"])
        )

        with _SkipPatches(stored_hash="STALE_HASH") as m:
            result = await process_service(
                service,
                session=_make_session(),
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
            )

        assert result == "processed"
        m["extract"].assert_called_once()
        m["delete"].assert_called_once()
        m["a"].assert_called_once()
        m["b"].assert_called_once()
        m["c"].assert_called_once()
        # 해시는 전 트랙 성공 후 identity metadata 에 찍힌다.
        m["update"].assert_called_once()
        assert m["update"].call_args[1]["source_hash"] == expected

    async def test_missing_source_hash_regenerates(self):
        """source_hash 없는 기존 적재분 → 전량 재생성."""
        with _SkipPatches(stored_hash=None) as m:
            result = await process_service(
                _make_service(),
                session=_make_session(),
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
            )

        assert result == "processed"
        m["extract"].assert_called_once()
        m["a"].assert_called_once()

    async def test_partial_tracks_never_skip(self):
        """부분 트랙 백필은 해시가 같아도 스킵하지 않는다."""
        service = _make_service()
        stored = compute_source_hash(
            service, clean_detail_content(service["detail_content"])
        )

        with _SkipPatches(stored_hash=stored) as m:
            result = await process_service(
                service,
                session=_make_session(),
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"B"},
            )

        assert result == "processed"
        m["extract"].assert_called_once()
        m["b"].assert_called_once()
        # 부분 트랙은 해시를 찍지 않는다(다른 트랙이 낡았을 수 있다).
        m["update"].assert_not_called()
        m["fetch"].assert_not_called()

    async def test_force_regenerates_despite_matching_hash(self):
        """force=True 면 해시가 같아도 재생성한다."""
        service = _make_service()
        stored = compute_source_hash(
            service, clean_detail_content(service["detail_content"])
        )

        with _SkipPatches(stored_hash=stored) as m:
            result = await process_service(
                service,
                session=_make_session(),
                embedder=MagicMock(),
                llm_client=MagicMock(),
                tracks={"A", "B", "C"},
                force=True,
            )

        assert result == "processed"
        m["extract"].assert_called_once()
        m["a"].assert_called_once()
        m["fetch"].assert_not_called()
