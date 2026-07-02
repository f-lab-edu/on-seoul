"""Langfuse 트레이스 추출 단위 테스트 — 픽스처 기반(라이브 접근 없음).

라이브 계약: 그래프(agents/graph.py::_langfuse_trace)는 질의·완료 메타데이터를
root "chat" SPAN observation 에 싣는다(트레이스 레벨이 아님 — Langfuse v4/OTel 에서
trace.input/metadata 는 비어 있을 수 있음). 따라서 추출기는 root span observation 의
input/metadata/output 을 읽되, trace_id 는 트레이스에서 가져온다.
이 테스트는 그 구조(_ObsView + trace_id 분리)를 그대로 모사한다.
"""

import json

from scripts.l1_eval.extract import (
    load_fixture_traces,
    pick_root_span,
    trace_to_signals,
)


class _ObsView:
    """langfuse ObservationsView 최소 모방(root span 의 속성 접근용)."""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.trace_id = kw.get("trace_id")
        self.type = kw.get("type", "SPAN")
        self.name = kw.get("name")
        self.parent_observation_id = kw.get("parent_observation_id")
        self.input = kw.get("input")
        self.output = kw.get("output")
        self.metadata = kw.get("metadata")


class _FullTrace:
    """langfuse TraceWithFullDetails 최소 모방(observations 중첩)."""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.observations = kw.get("observations", [])


class TestTraceToSignals:
    def test_extracts_core_metadata_from_span(self):
        span = _ObsView(
            id="obs-1",
            trace_id="tr-1",
            name="chat",
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
        sig = trace_to_signals(trace_id="tr-1", span=span)
        # trace_id 는 트레이스에서(observation.id 아님).
        assert sig.trace_id == "tr-1"
        assert sig.raw_query == "강남 수영장 알려줘"
        assert sig.intent == "SQL_SEARCH"
        assert sig.action == "RETRIEVE"
        assert sig.retry_count == 0

    def test_dict_input_message_extracted(self):
        # input 이 dict({"message": ...}) 형태일 수 있다.
        span = _ObsView(input={"message": "무료 전시"}, metadata={})
        sig = trace_to_signals(trace_id="tr-2", span=span)
        assert sig.raw_query == "무료 전시"

    def test_missing_span_degrades_gracefully(self):
        # root span 을 못 찾으면(구 트레이스/누락) 신호 전부 기본값, trace_id 만 유지.
        sig = trace_to_signals(trace_id="tr-3", span=None)
        assert sig.trace_id == "tr-3"
        assert sig.raw_query == ""
        assert sig.intent is None
        assert sig.retry_count == 0

    def test_forced_intent_and_quality_signals(self):
        span = _ObsView(
            input="q",
            metadata={
                "intent": "VECTOR_SEARCH",
                "forced_intent": "VECTOR_SEARCH",
                "retry_count": 1,
                "result_quality": {"thin": True},
                "total_hits": 2,
            },
        )
        sig = trace_to_signals(trace_id="tr-4", span=span)
        assert sig.forced_intent == "VECTOR_SEARCH"
        assert sig.retry_count == 1
        assert sig.is_thin()
        assert sig.total_hits == 2


class TestPickRootSpan:
    def test_picks_named_root_span(self):
        root = _ObsView(id="a", name="chat", parent_observation_id=None)
        child = _ObsView(id="b", name="router", parent_observation_id="a")
        trace = _FullTrace(id="tr", observations=[child, root])
        picked = pick_root_span(trace, span_name="chat")
        assert picked is root

    def test_prefers_name_over_parentless_other(self):
        # 이름이 정확히 일치하는 span 을 우선(다른 parentless span 이 있어도).
        other = _ObsView(id="x", name="misc", parent_observation_id=None)
        root = _ObsView(id="a", name="chat", parent_observation_id=None)
        trace = _FullTrace(id="tr", observations=[other, root])
        assert pick_root_span(trace, span_name="chat") is root

    def test_falls_back_to_parentless_span_when_name_absent(self):
        # 이름이 안 맞으면 부모 없는 최상위 span 으로 폴백.
        root = _ObsView(id="a", name="root", parent_observation_id=None, type="SPAN")
        child = _ObsView(id="b", name="x", parent_observation_id="a")
        trace = _FullTrace(id="tr", observations=[child, root])
        assert pick_root_span(trace, span_name="chat") is root

    def test_returns_none_when_no_observations(self):
        assert pick_root_span(_FullTrace(id="tr", observations=[]), span_name="chat") is None


class TestFixtureLoading:
    def test_load_bundled_fixture(self):
        sigs = load_fixture_traces()
        assert len(sigs) >= 5
        assert all(s.raw_query for s in sigs)
        # 대표 실패 유형이 픽스처에 최소 1건씩 포함되어야 파이프라인 데모가 된다.
        assert any(s.is_zero_hit() for s in sigs)
        assert any(s.is_thin() for s in sigs)

    def test_fixture_uses_observation_shape(self):
        # 픽스처가 라이브 계약(observations 중첩)을 그대로 쓴다.
        sigs = load_fixture_traces()
        by_id = {s.trace_id: s for s in sigs}
        # 정상 트레이스에서 신호가 실제 값으로 읽힌다.
        assert by_id["fx-normal-1"].intent == "SQL_SEARCH"
        assert by_id["fx-normal-1"].sql_hits == 6
        # 구(舊) 트레이스(신호 부재)는 관대하게 흡수.
        assert by_id["fx-old-1"].intent == "SQL_SEARCH"

    def test_load_custom_fixture_path(self, tmp_path):
        p = tmp_path / "traces.json"
        p.write_text(
            json.dumps(
                [
                    {
                        "id": "x",
                        "observations": [
                            {
                                "id": "obs-x",
                                "trace_id": "x",
                                "type": "SPAN",
                                "name": "chat",
                                "parent_observation_id": None,
                                "input": "hi",
                                "metadata": {"intent": "MAP"},
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sigs = load_fixture_traces(p)
        assert len(sigs) == 1
        assert sigs[0].trace_id == "x"
        assert sigs[0].raw_query == "hi"
        assert sigs[0].intent == "MAP"
