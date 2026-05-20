"""scripts/tracks/identity.py 단위 테스트."""

import json
from unittest.mock import AsyncMock, MagicMock


from llm.extractor import ExtractedMetadata
from scripts.tracks._shared import ServiceRecord
from scripts.tracks.identity import embed_and_insert_identity


def _make_service(**kwargs) -> ServiceRecord:
    base: ServiceRecord = {
        "service_id": "S001",
        "service_name": "강남헬스장",
        "service_gubun": "체육",
        "area_name": "강남구",
        "max_class_name": "체육시설",
        "min_class_name": "헬스장",
        "place_name": "강남헬스",
        "target_info": "성인",
        "payment_type": "무료",
        "service_status": "접수중",
        "service_url": None,
        "detail_content": None,
        "receipt_start_dt": None,
        "receipt_end_dt": None,
        "service_open_start_dt": None,
        "service_open_end_dt": None,
        "coord_x": None,
        "coord_y": None,
    }
    base.update(kwargs)
    return base


def _make_embedder(vector: list[float] = None):
    embedder = MagicMock()
    embedder.aembed_query = AsyncMock(return_value=vector or [0.1, 0.2, 0.3])
    return embedder


def _make_session():
    session = MagicMock()
    session.execute = AsyncMock()
    return session


def _make_extracted() -> ExtractedMetadata:
    return ExtractedMetadata(summary="강남구 헬스장 시설", fee="무료")


class TestEmbedAndInsertIdentity:
    async def test_calls_aembed_query_with_identity_text(self):
        """embedding_text가 area_name+max_class_name+min_class_name+service_name+place_name 조합이다."""
        service = _make_service()
        embedder = _make_embedder()
        session = _make_session()

        await embed_and_insert_identity(
            session, service, embedder=embedder, extracted=_make_extracted()
        )

        call_text = embedder.aembed_query.call_args[0][0]
        assert "강남구" in call_text
        assert "체육시설" in call_text
        assert "강남헬스장" in call_text

    async def test_session_execute_called_once(self):
        """session.execute가 1회 호출된다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_identity(
            session, service, embedder=_make_embedder(), extracted=_make_extracted()
        )

        session.execute.assert_called_once()

    async def test_bind_params_include_row_kind_identity(self):
        """bind 파라미터에 row_kind='identity', idx=0이 포함된다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_identity(
            session, service, embedder=_make_embedder(), extracted=_make_extracted()
        )

        bind = session.execute.call_args[0][1]
        assert bind["row_kind"] == "identity"
        assert bind["idx"] == 0
        assert bind["service_id"] == "S001"

    async def test_metadata_contains_extracted(self):
        """metadata JSON에 'extracted' 키가 포함된다."""
        service = _make_service()
        session = _make_session()
        extracted = _make_extracted()

        await embed_and_insert_identity(
            session, service, embedder=_make_embedder(), extracted=extracted
        )

        bind = session.execute.call_args[0][1]
        meta = json.loads(bind["metadata"])
        assert "extracted" in meta
        assert meta["extracted"]["summary"] == "강남구 헬스장 시설"

    async def test_extracted_none_metadata_extracted_is_null(self):
        """extracted가 None이면 metadata['extracted']가 None이다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_identity(
            session, service, embedder=_make_embedder(), extracted=None
        )

        bind = session.execute.call_args[0][1]
        meta = json.loads(bind["metadata"])
        assert meta["extracted"] is None

    async def test_intent_label_is_none(self):
        """identity 행의 intent_label은 None이다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_identity(
            session, service, embedder=_make_embedder(), extracted=_make_extracted()
        )

        bind = session.execute.call_args[0][1]
        assert bind["intent_label"] is None
