"""llm/hyqe.py 단위 테스트."""

from unittest.mock import MagicMock, patch

from langchain_core.runnables import RunnablePassthrough

from llm.hyqe import HyQEQuestion, _enforce_distribution, generate_questions

_PASSTHROUGH = RunnablePassthrough()
_PATCH_PROMPT = patch("llm.hyqe.HYQE_PROMPT", _PASSTHROUGH)


def _make_questions(semantic: int, detail: int, keyword: int) -> list[HyQEQuestion]:
    qs = []
    for i in range(semantic):
        qs.append(HyQEQuestion(question_text=f"semantic {i}", intent_label="semantic"))
    for i in range(detail):
        qs.append(HyQEQuestion(question_text=f"detail {i}", intent_label="detail"))
    for i in range(keyword):
        qs.append(HyQEQuestion(question_text=f"keyword {i}", intent_label="keyword"))
    return qs


def _make_llm_returning(questions: list[HyQEQuestion]):
    """questions 리스트를 반환하는 llm_client mock."""
    from langchain_core.runnables import RunnableLambda

    async def _fn(_input):
        return [q.model_dump() for q in questions]

    runnable = RunnableLambda(_fn)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=runnable)
    return llm


def _make_llm_failing():
    """ainvoke가 예외를 발생시키는 llm_client mock."""
    from langchain_core.runnables import RunnableLambda

    async def _fn(_input):
        raise RuntimeError("LLM 오류")

    runnable = RunnableLambda(_fn)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=runnable)
    return llm


_COMMON_KWARGS = dict(
    service_name="강남헬스",
    area_name="강남구",
    max_class_name="체육",
    min_class_name="헬스장",
    cleaned_detail="운동 시설입니다.",
    extracted_summary="강남구 헬스장",
    n=10,
)


class TestGenerateQuestions:
    async def test_returns_n_questions(self):
        """분포가 맞는 10개 질문을 반환하면 10개를 반환한다."""
        # semantic 5, detail 3, keyword 2 → 정확히 50/30/20
        qs = _make_questions(5, 3, 2)
        llm = _make_llm_returning(qs)

        with _PATCH_PROMPT:
            result = await generate_questions(**_COMMON_KWARGS, llm_client=llm)

        assert len(result) == 10

    async def test_llm_failure_returns_empty_list(self):
        """LLM 실패 시 빈 리스트를 반환한다 (예외 전파 없음)."""
        llm = _make_llm_failing()

        with _PATCH_PROMPT:
            result = await generate_questions(**_COMMON_KWARGS, llm_client=llm)

        assert result == []


class TestEnforceDistribution:
    def test_trims_excess_semantic(self):
        """semantic이 목표보다 많으면 초과분을 제거하고 n개를 반환한다."""
        # n=10, target: semantic=5, detail=3, keyword=2
        # 입력: semantic=8, detail=1, keyword=1 → semantic 초과
        questions = _make_questions(8, 1, 1)
        result = _enforce_distribution(questions, n=10)

        assert len(result) == 10
        semantic_count = sum(1 for q in result if q.intent_label == "semantic")
        assert semantic_count == 5

    def test_pads_missing_keyword_with_template(self):
        """keyword가 부족하면 템플릿 질문으로 채워 n개를 반환한다."""
        # n=10, target: semantic=5, detail=3, keyword=2
        # 입력: semantic=5, detail=3, keyword=0 → keyword 부족
        questions = _make_questions(5, 3, 0)
        result = _enforce_distribution(questions, n=10)

        assert len(result) == 10
        keyword_count = sum(1 for q in result if q.intent_label == "keyword")
        assert keyword_count == 2
