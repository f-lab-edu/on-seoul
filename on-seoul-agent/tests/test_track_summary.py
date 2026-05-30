"""scripts/tracks/summary.py 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock


from llm.extractor import ExtractedMetadata
from scripts.tracks._shared import ServiceRecord
from scripts.tracks.summary import embed_and_insert_summary


def _make_service() -> ServiceRecord:
    return {
        "service_id": "S001",
        "service_name": "강남헬스장",
    }


def _make_extracted(summary: str = "강남구 헬스장") -> ExtractedMetadata:
    return ExtractedMetadata(summary=summary)


def _make_embedder(vector: list[float] = None):
    embedder = MagicMock()
    embedder.aembed_query = AsyncMock(return_value=vector or [0.1, 0.2])
    return embedder


def _make_session():
    session = MagicMock()
    session.execute = AsyncMock()
    return session


class TestEmbedAndInsertSummary:
    async def test_embedding_text_is_summary(self):
        """embedding_text가 extracted.summary와 일치한다."""
        service = _make_service()
        extracted = _make_extracted("강남구 헬스장 시설 요약")
        embedder = _make_embedder()
        session = _make_session()

        await embed_and_insert_summary(
            session, service, embedder=embedder, extracted=extracted
        )

        call_text = embedder.aembed_query.call_args[0][0]
        assert call_text == "강남구 헬스장 시설 요약"

    async def test_row_kind_is_summary(self):
        """bind 파라미터에 row_kind='summary', idx=0이 포함된다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_summary(
            session, service, embedder=_make_embedder(), extracted=_make_extracted()
        )

        bind = session.execute.call_args[0][1]
        assert bind["row_kind"] == "summary"
        assert bind["idx"] == 0

    async def test_metadata_and_intent_label_are_none(self):
        """summary 행의 metadata와 intent_label은 None이다."""
        service = _make_service()
        session = _make_session()

        await embed_and_insert_summary(
            session, service, embedder=_make_embedder(), extracted=_make_extracted()
        )

        bind = session.execute.call_args[0][1]
        assert bind["metadata"] is None
        assert bind["intent_label"] is None
