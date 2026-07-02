"""LLM 판단 라벨 분류기 단위 테스트 — fake LLM 주입(실 호출 금지)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.eval.l1.llm_classifier import (
    ClassifierOutput,
    FailureClassifier,
)
from scripts.eval.l1.signals import LlmBucket, QuerySignals


def _fake_model(output: ClassifierOutput) -> MagicMock:
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=output)
    model = MagicMock()
    model.with_structured_output = MagicMock(return_value=structured)
    return model


def _sig(**kw) -> QuerySignals:
    base = {"trace_id": "t1", "raw_query": "강남 무료 실내 수영장 주차 가능"}
    base.update(kw)
    return QuerySignals(**base)


class TestFailureClassifier:
    async def test_intent_mispick(self):
        out = ClassifierOutput(
            bucket=LlmBucket.INTENT_MISPICK, rationale="집계 질의를 SQL 검색으로 오선택"
        )
        clf = FailureClassifier(model=_fake_model(out))
        result = await clf.classify(_sig(intent="SQL_SEARCH"))
        assert result.bucket is LlmBucket.INTENT_MISPICK
        assert result.rationale

    async def test_drift(self):
        out = ClassifierOutput(bucket=LlmBucket.DRIFT, rationale="자연 활동인데 실내 강좌 혼입")
        clf = FailureClassifier(model=_fake_model(out))
        result = await clf.classify(_sig())
        assert result.bucket is LlmBucket.DRIFT

    async def test_compound_unexpressible(self):
        out = ClassifierOutput(
            bucket=LlmBucket.COMPOUND_UNEXPRESSIBLE, rationale="제약 4개 — 단일 intent 불가"
        )
        clf = FailureClassifier(model=_fake_model(out))
        result = await clf.classify(_sig(applied_filter_count=1))
        assert result.bucket is LlmBucket.COMPOUND_UNEXPRESSIBLE

    async def test_fail_open_returns_normal(self):
        # LLM 예외 시 파이프라인이 죽지 않고 NORMAL(판단 불가)로 폴백.
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        model = MagicMock()
        model.with_structured_output = MagicMock(return_value=structured)
        clf = FailureClassifier(model=model)
        result = await clf.classify(_sig())
        assert result.bucket is LlmBucket.NORMAL
        assert result.rationale is not None

    async def test_message_includes_constraint_vs_filter_signal(self):
        out = ClassifierOutput(bucket=LlmBucket.NORMAL, rationale="정상")
        model = _fake_model(out)
        clf = FailureClassifier(model=model)
        await clf.classify(_sig(applied_filter_count=1))
        structured = model.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]
        joined = " ".join(getattr(m, "content", "") for m in messages)
        # 질의 원문과 적용 필터 수가 프롬프트에 실려야 한다.
        assert "강남" in joined
        assert "1" in joined

    @pytest.mark.parametrize("bucket", list(LlmBucket))
    async def test_all_buckets_roundtrip(self, bucket: LlmBucket):
        out = ClassifierOutput(bucket=bucket, rationale="r")
        clf = FailureClassifier(model=_fake_model(out))
        result = await clf.classify(_sig())
        assert result.bucket is bucket
