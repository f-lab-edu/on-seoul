"""scripts/tracks/questions.py 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch


from llm.hyqe import HyQEQuestion
from scripts.tracks._shared import ServiceRecord
from scripts.tracks.questions import embed_and_insert_questions


def _make_service() -> ServiceRecord:
    return {
        "service_id": "S001",
        "service_name": "강남헬스장",
        "area_name": "강남구",
        "max_class_name": "체육시설",
        "min_class_name": "헬스장",
    }


def _make_embedder():
    embedder = MagicMock()
    embedder.aembed_query = AsyncMock(return_value=[0.1, 0.2])
    return embedder


def _make_llm():
    return MagicMock()


def _make_session():
    session = MagicMock()
    session.execute = AsyncMock()
    return session


def _make_questions(n: int = 3) -> list[HyQEQuestion]:
    labels = ["semantic", "detail", "keyword"]
    return [
        HyQEQuestion(question_text=f"질문 {i}", intent_label=labels[i % 3])
        for i in range(n)
    ]


class TestEmbedAndInsertQuestions:
    async def test_inserts_one_row_per_question(self):
        """질문 수만큼 session.execute가 호출된다."""
        questions = _make_questions(3)
        service = _make_service()
        session = _make_session()

        with patch("scripts.tracks.questions.generate_questions", AsyncMock(return_value=questions)):
            result = await embed_and_insert_questions(
                session, service,
                embedder=_make_embedder(),
                llm_client=_make_llm(),
                cleaned_detail="상세 내용",
                extracted_summary="요약",
            )

        assert result is True
        assert session.execute.call_count == 3

    async def test_bind_params_row_kind_question(self):
        """각 행의 row_kind='question', intent_label이 HyQEQuestion에서 가져온 값이다."""
        questions = [HyQEQuestion(question_text="의미 질문", intent_label="semantic")]
        service = _make_service()
        session = _make_session()

        with patch("scripts.tracks.questions.generate_questions", AsyncMock(return_value=questions)):
            await embed_and_insert_questions(
                session, service,
                embedder=_make_embedder(),
                llm_client=_make_llm(),
                cleaned_detail="",
                extracted_summary="요약",
            )

        bind = session.execute.call_args[0][1]
        assert bind["row_kind"] == "question"
        assert bind["intent_label"] == "semantic"
        assert bind["idx"] == 0

    async def test_returns_false_when_no_questions_generated(self):
        """generate_questions가 빈 리스트를 반환하면 False를 반환한다."""
        service = _make_service()
        session = _make_session()

        with patch("scripts.tracks.questions.generate_questions", AsyncMock(return_value=[])):
            result = await embed_and_insert_questions(
                session, service,
                embedder=_make_embedder(),
                llm_client=_make_llm(),
                cleaned_detail="",
                extracted_summary="요약",
            )

        assert result is False
        session.execute.assert_not_called()

    async def test_idx_increments_per_question(self):
        """여러 질문의 idx가 0부터 순차 증가한다."""
        questions = _make_questions(3)
        service = _make_service()
        session = _make_session()
        captured_binds = []

        async def _capture_execute(stmt, bind):
            captured_binds.append(bind)

        session.execute = AsyncMock(side_effect=_capture_execute)

        with patch("scripts.tracks.questions.generate_questions", AsyncMock(return_value=questions)):
            await embed_and_insert_questions(
                session, service,
                embedder=_make_embedder(),
                llm_client=_make_llm(),
                cleaned_detail="",
                extracted_summary="요약",
            )

        assert [b["idx"] for b in captured_binds] == [0, 1, 2]
