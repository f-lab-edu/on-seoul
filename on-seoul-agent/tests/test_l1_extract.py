"""Langfuse 트레이스 추출 단위 테스트 — 픽스처 기반(라이브 접근 없음)."""

import json

from scripts.l1_eval.extract import (
    load_fixture_traces,
    trace_to_signals,
)


class _FakeTrace:
    """langfuse TraceWithDetails 최소 모방(속성 접근용)."""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.input = kw.get("input")
        self.output = kw.get("output")
        self.metadata = kw.get("metadata")


class TestTraceToSignals:
    def test_extracts_core_metadata(self):
        t = _FakeTrace(
            id="tr-1",
            input="강남 수영장 알려줘",
            output="...",
            metadata={
                "intent": "SQL_SEARCH",
                "action": "RETRIEVE",
                "node_path": ["intake", "router", "sql", "answer"],
                "retry_count": 0,
                "cache_hit": False,
            },
        )
        sig = trace_to_signals(t)
        assert sig.trace_id == "tr-1"
        assert sig.raw_query == "강남 수영장 알려줘"
        assert sig.intent == "SQL_SEARCH"
        assert sig.action == "RETRIEVE"
        assert sig.retry_count == 0

    def test_dict_input_message_extracted(self):
        # input 이 dict({"message": ...}) 형태일 수 있다.
        t = _FakeTrace(id="tr-2", input={"message": "무료 전시"}, metadata={})
        sig = trace_to_signals(t)
        assert sig.raw_query == "무료 전시"

    def test_missing_metadata_degrades_gracefully(self):
        t = _FakeTrace(id="tr-3", input="질의", metadata=None)
        sig = trace_to_signals(t)
        assert sig.trace_id == "tr-3"
        assert sig.intent is None
        assert sig.retry_count == 0

    def test_forced_intent_and_quality_signals(self):
        t = _FakeTrace(
            id="tr-4",
            input="q",
            metadata={
                "intent": "VECTOR_SEARCH",
                "forced_intent": "VECTOR_SEARCH",
                "retry_count": 1,
                "result_quality": {"thin": True},
                "total_hits": 2,
            },
        )
        sig = trace_to_signals(t)
        assert sig.forced_intent == "VECTOR_SEARCH"
        assert sig.retry_count == 1
        assert sig.is_thin()
        assert sig.total_hits == 2


class TestFixtureLoading:
    def test_load_bundled_fixture(self):
        sigs = load_fixture_traces()
        assert len(sigs) >= 5
        assert all(s.raw_query for s in sigs)
        # 대표 실패 유형이 픽스처에 최소 1건씩 포함되어야 파이프라인 데모가 된다.
        assert any(s.is_zero_hit() for s in sigs)
        assert any(s.is_thin() for s in sigs)

    def test_load_custom_fixture_path(self, tmp_path):
        p = tmp_path / "traces.json"
        p.write_text(
            json.dumps(
                [{"id": "x", "input": "hi", "metadata": {"intent": "MAP"}}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sigs = load_fixture_traces(p)
        assert len(sigs) == 1
        assert sigs[0].intent == "MAP"
