"""llm/extractor.py 단위 테스트.

실제 LLM 호출 없이 패치로 경로 검증.

전략:
  EXTRACTION_PROMPT_FULL / METADATA_ONLY 를 RunnablePassthrough 로 패치하면
  chain = passthrough | llm.with_structured_output(ExtractedMetadata) 가
  chain = passthrough | runnable_chain 이 되어 RunnableSequence(passthrough, runnable_chain) 이 된다.
  runnable_chain.ainvoke 가 호출될 때 테스트 return_value / side_effect 를 반환하도록
  RunnableLambda 로 래핑한다.
"""

from unittest.mock import MagicMock, patch

from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from llm.embedding_config import EXTRACTION_MIN_CHARS as MIN_CHARS
from llm.extractor import ExtractedMetadata, extract_metadata

_PASSTHROUGH = RunnablePassthrough()

_LONG_DETAIL = "A" * MIN_CHARS
_SHORT_DETAIL = "A" * (MIN_CHARS - 1)


def _sample_extracted() -> ExtractedMetadata:
    return ExtractedMetadata(
        fee="무료",
        operating_hours="평일 09:00-18:00",
        summary="강남구 헬스장 시설",
    )


class _CallCounter:
    count: int = 0


def _make_runnable_chain(return_value=None, side_effect=None):
    """RunnableLambda 기반 체인. call_count를 외부에서 관찰할 수 있다."""
    counter = _CallCounter()

    if side_effect is not None:
        exc = side_effect

        async def _fn(_input):
            counter.count += 1
            raise exc

    else:
        val = return_value

        async def _fn(_input):
            counter.count += 1
            return val

    runnable = RunnableLambda(_fn)
    runnable._counter = counter
    return runnable


def _make_llm_client(return_value=None, side_effect=None):
    """llm_client mock: with_structured_output이 RunnableLambda chain을 반환한다."""
    runnable = _make_runnable_chain(return_value=return_value, side_effect=side_effect)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=runnable)
    return llm, runnable


_PATCH_FULL = patch("llm.extractor.EXTRACTION_PROMPT_FULL", _PASSTHROUGH)
_PATCH_META = patch("llm.extractor.EXTRACTION_PROMPT_METADATA_ONLY", _PASSTHROUGH)


class TestExtractMetadataFullPath:
    async def test_normal_path_returns_extracted(self):
        """정상 경로: LLM이 ExtractedMetadata를 반환하면 그대로 반환한다."""
        expected = _sample_extracted()
        llm, chain = _make_llm_client(return_value=expected)

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail=_LONG_DETAIL,
                llm_client=llm,
            )

        assert result is not None
        assert result.fee == "무료"
        assert result.summary == "강남구 헬스장 시설"

    async def test_returns_none_on_llm_failure(self):
        """LLM이 두 번 모두 실패하면 None을 반환한다."""
        llm, chain = _make_llm_client(side_effect=RuntimeError("LLM 오류"))

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail=_LONG_DETAIL,
                llm_client=llm,
            )

        assert result is None


class TestExtractMetadataFallbackPath:
    async def test_empty_detail_uses_fallback_path(self):
        """cleaned_detail이 빈 문자열이면 METADATA_ONLY 프롬프트 경로를 사용한다."""
        expected = _sample_extracted()
        llm, chain = _make_llm_client(return_value=expected)

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail="",
                llm_client=llm,
            )

        assert result is not None
        llm.with_structured_output.assert_called_once_with(ExtractedMetadata)

    async def test_short_detail_below_threshold_uses_fallback(self):
        """cleaned_detail 길이가 MIN_CHARS 미만이면 METADATA_ONLY 경로를 탄다."""
        expected = _sample_extracted()
        llm, chain = _make_llm_client(return_value=expected)

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail=_SHORT_DETAIL,
                llm_client=llm,
            )

        assert result is not None


class TestExtractMetadataLlmFailure:
    async def test_llm_failure_returns_none(self):
        """LLM 실패 시 None이 반환된다 (예외 전파 없음)."""
        llm, chain = _make_llm_client(side_effect=Exception("연결 오류"))

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail=_LONG_DETAIL,
                llm_client=llm,
            )

        assert result is None

    async def test_retry_once_before_returning_none(self):
        """LLM이 연속 실패 시 총 2회 호출(1회 원본 + 1회 재시도) 후 None 반환."""
        llm, chain = _make_llm_client(side_effect=RuntimeError("오류"))

        with _PATCH_FULL, _PATCH_META:
            result = await extract_metadata(
                service_name="강남헬스",
                cleaned_detail=_LONG_DETAIL,
                llm_client=llm,
            )

        assert result is None
        assert chain._counter.count == 2
