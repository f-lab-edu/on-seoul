"""L1 Phase 6 critic 동작 지표 측정 하네스 단위 테스트 — 픽스처 기반(라이브 없음).

관측 계약: critic 이 발동하면 root "chat" span 안에 자식 SPAN(name="retrieval_critic")이
라운드마다 열리고 metadata {entry_signal, decision, round} 를 담는다(Phase 5,
agents/graph.py::record_critic_span). 이 테스트는 그 중첩 구조를 그대로 모사해
pick_critic_spans → trace_to_critic → compute_metrics 파이프라인을 검증한다.
"""

from __future__ import annotations

import subprocess
import sys

from scripts.eval.l1.critic_metrics import (
    CriticMetrics,
    CriticRound,
    CriticTrace,
    compute_metrics,
    format_report,
    load_fixture_critic_traces,
    trace_to_critic,
)
from scripts.eval.l1.extract import _DictTrace, pick_critic_spans
from scripts.eval.l1.signals import QuerySignals


def _sig(**kw) -> QuerySignals:
    base = {"trace_id": kw.pop("trace_id", "t"), "raw_query": "q"}
    base.update(kw)
    return QuerySignals(**base)


def _ct(**kw) -> CriticTrace:
    return CriticTrace(signals=_sig(**kw.pop("sig", {})), **kw)


# ── extract 계약: critic 자식 스팬 읽기 ────────────────────────────────────


class TestPickCriticSpans:
    def test_collects_named_child_spans_round_sorted(self):
        trace = _DictTrace(
            {
                "id": "t",
                "observations": [
                    {"id": "root", "type": "SPAN", "name": "chat"},
                    {
                        "id": "c2",
                        "type": "SPAN",
                        "name": "retrieval_critic",
                        "parent_observation_id": "root",
                        "metadata": {"round": 2, "decision": "ANSWER"},
                    },
                    {
                        "id": "c1",
                        "type": "SPAN",
                        "name": "retrieval_critic",
                        "parent_observation_id": "root",
                        "metadata": {"round": 1, "decision": "REPLAN"},
                    },
                ],
            }
        )
        spans = pick_critic_spans(trace)
        assert [s.metadata["round"] for s in spans] == [1, 2]

    def test_no_critic_spans_returns_empty(self):
        trace = _DictTrace(
            {"id": "t", "observations": [{"id": "root", "type": "SPAN", "name": "chat"}]}
        )
        assert pick_critic_spans(trace) == []

    def test_ignores_other_child_spans(self):
        trace = _DictTrace(
            {
                "id": "t",
                "observations": [
                    {"id": "root", "type": "SPAN", "name": "chat"},
                    {"id": "r", "type": "GENERATION", "name": "router"},
                ],
            }
        )
        assert pick_critic_spans(trace) == []


# ── 구조화: trace_to_critic ────────────────────────────────────────────────


class TestTraceToCritic:
    def test_entered_trace_has_rounds_and_signals(self):
        trace = _DictTrace(
            {
                "id": "t",
                "observations": [
                    {
                        "id": "root",
                        "type": "SPAN",
                        "name": "chat",
                        "input": "q",
                        "metadata": {"action": "RETRIEVE", "total_hits": 3},
                    },
                    {
                        "id": "c1",
                        "type": "SPAN",
                        "name": "retrieval_critic",
                        "parent_observation_id": "root",
                        "latency": 0.9,
                        "metadata": {"entry_signal": "zero", "decision": "REPLAN", "round": 1},
                    },
                ],
            }
        )
        ct = trace_to_critic(trace)
        assert ct.entered() is True
        assert ct.signals.total_hits == 3
        assert ct.rounds[0].entry_signal == "zero"
        assert ct.rounds[0].decision == "REPLAN"
        assert ct.rounds[0].latency_s == 0.9

    def test_non_entered_trace_has_no_rounds(self):
        trace = _DictTrace(
            {
                "id": "t",
                "observations": [
                    {
                        "id": "root",
                        "type": "SPAN",
                        "name": "chat",
                        "input": "q",
                        "metadata": {"action": "RETRIEVE", "total_hits": 6},
                    }
                ],
            }
        )
        ct = trace_to_critic(trace)
        assert ct.entered() is False
        assert ct.rounds == []

    def test_latency_from_start_end_when_no_latency_field(self):
        trace = _DictTrace(
            {
                "id": "t",
                "observations": [
                    {"id": "root", "type": "SPAN", "name": "chat", "metadata": {}},
                    {
                        "id": "c1",
                        "type": "SPAN",
                        "name": "retrieval_critic",
                        "parent_observation_id": "root",
                        "start_time": "2026-07-04T10:00:00.000Z",
                        "end_time": "2026-07-04T10:00:00.750Z",
                        "metadata": {"decision": "ANSWER", "round": 1},
                    },
                ],
            }
        )
        ct = trace_to_critic(trace)
        assert abs(ct.rounds[0].latency_s - 0.75) < 1e-6


# ── 복구율 ────────────────────────────────────────────────────────────────


class TestCriticTraceHelpers:
    def test_recovered_true_when_hits_and_not_thin(self):
        assert _ct(sig={"total_hits": 3, "result_quality": {"thin": False}}).recovered()

    def test_not_recovered_when_zero_hits(self):
        assert _ct(sig={"total_hits": 0}).recovered() is False

    def test_not_recovered_when_thin(self):
        assert (
            _ct(sig={"total_hits": 1, "result_quality": {"thin": True}}).recovered()
            is False
        )

    def test_had_replan(self):
        ct = _ct(rounds=[CriticRound(decision="REPLAN", round=1)])
        assert ct.had_replan() is True
        assert _ct(rounds=[CriticRound(decision="ANSWER", round=1)]).had_replan() is False


# ── 지표 산출 ──────────────────────────────────────────────────────────────


def _retrieve(entered: bool, **sig) -> CriticTrace:
    sig.setdefault("action", "RETRIEVE")
    rounds = sig.pop("rounds", [])
    return CriticTrace(signals=_sig(**sig), rounds=rounds)


class TestComputeMetrics:
    def test_activation_and_path_preserved(self):
        traces = [
            _retrieve(True, total_hits=3, rounds=[CriticRound(decision="ANSWER", round=1)]),
            _retrieve(False, total_hits=6),
            _retrieve(False, total_hits=8),
            _retrieve(False, total_hits=4),
        ]
        m = compute_metrics(traces)
        assert m.retrieval_total == 4
        assert m.critic_entered == 1
        assert m.activation_rate == 0.25
        assert m.path_preserved == 3
        assert m.path_preserved_rate == 0.75

    def test_non_retrieve_excluded_from_denominator(self):
        traces = [
            _retrieve(False, total_hits=6),
            CriticTrace(signals=_sig(action="DIRECT_ANSWER")),
        ]
        m = compute_metrics(traces)
        assert m.total == 2
        assert m.retrieval_total == 1
        assert m.non_retrieve_total == 1

    def test_decision_distribution_per_round(self):
        traces = [
            _retrieve(
                True,
                total_hits=2,
                rounds=[
                    CriticRound(decision="REPLAN", round=1),
                    CriticRound(decision="ANSWER", round=2),
                ],
            ),
            _retrieve(True, total_hits=0, rounds=[CriticRound(decision="STOP", round=1)]),
        ]
        m = compute_metrics(traces)
        assert m.round_total == 3
        assert m.decision_counts == {"REPLAN": 1, "ANSWER": 1, "STOP": 1}

    def test_undecided_bucket_for_none_decision(self):
        traces = [_retrieve(True, total_hits=1, rounds=[CriticRound(decision=None, round=1)])]
        m = compute_metrics(traces)
        assert m.decision_counts == {"UNDECIDED": 1}

    def test_replan_recovery_rate_mixed(self):
        traces = [
            # REPLAN → 복구(hits>0, not thin)
            _retrieve(
                True,
                total_hits=3,
                result_quality={"thin": False},
                rounds=[CriticRound(decision="REPLAN", round=1)],
            ),
            # REPLAN → 미복구(0건)
            _retrieve(True, total_hits=0, rounds=[CriticRound(decision="REPLAN", round=1)]),
            # REPLAN → 미복구(thin)
            _retrieve(
                True,
                total_hits=1,
                result_quality={"thin": True},
                rounds=[CriticRound(decision="REPLAN", round=1)],
            ),
            # ANSWER only(REPLAN 없음) → 복구율 분모에서 제외
            _retrieve(True, total_hits=5, rounds=[CriticRound(decision="ANSWER", round=1)]),
        ]
        m = compute_metrics(traces)
        assert m.replan_traces == 3
        assert m.replan_recovered == 1
        assert abs(m.replan_recovery_rate - 1 / 3) < 1e-6

    def test_replan_recovery_none_when_no_replan(self):
        traces = [_retrieve(True, total_hits=5, rounds=[CriticRound(decision="ANSWER", round=1)])]
        m = compute_metrics(traces)
        assert m.replan_traces == 0
        assert m.replan_recovery_rate is None

    def test_entry_signal_counts(self):
        traces = [
            _retrieve(True, total_hits=1, rounds=[CriticRound(entry_signal="zero", round=1)]),
            _retrieve(True, total_hits=1, rounds=[CriticRound(entry_signal="thin", round=1)]),
            _retrieve(True, total_hits=1, rounds=[CriticRound(entry_signal="zero", round=1)]),
        ]
        m = compute_metrics(traces)
        assert m.entry_signal_counts == {"zero": 2, "thin": 1}

    def test_latency_present_and_compared(self):
        traces = [
            _retrieve(
                True,
                total_hits=3,
                rounds=[CriticRound(decision="REPLAN", round=1, latency_s=0.9)],
            ),
            _retrieve(False, total_hits=6),
        ]
        # total_latency 부착.
        traces[0].total_latency_s = 5.0
        traces[1].total_latency_s = 2.0
        m = compute_metrics(traces)
        assert m.critic_latency_samples == 1
        assert m.critic_latency_mean_s == 0.9
        assert m.entered_total_latency_mean_s == 5.0
        assert m.not_entered_total_latency_mean_s == 2.0

    def test_latency_absent_degrades_gracefully(self):
        traces = [
            _retrieve(True, total_hits=1, rounds=[CriticRound(decision="ANSWER", round=1)]),
            _retrieve(False, total_hits=6),
        ]
        m = compute_metrics(traces)
        assert m.critic_latency_samples == 0
        assert m.critic_latency_mean_s is None
        assert m.entered_total_latency_mean_s is None
        # 리포트가 결측을 명시적으로 흡수한다.
        report = format_report(m)
        assert "타이밍 신호 없음" in report

    def test_empty_traces_no_zero_division(self):
        m = compute_metrics([])
        assert m.activation_rate == 0.0
        assert m.path_preserved_rate == 0.0
        assert m.replan_recovery_rate is None


# ── 번들 픽스처 (라이브 구조 부합) ──────────────────────────────────────────


class TestBundledFixture:
    def test_loads_and_distinguishes_entered(self):
        traces = load_fixture_critic_traces()
        assert len(traces) >= 8
        entered = [t for t in traces if t.entered()]
        not_entered = [t for t in traces if t.entered() is False]
        assert entered and not_entered  # 두 경로 모두 데모.

    def test_fixture_metrics_end_to_end(self):
        m = compute_metrics(load_fixture_critic_traces())
        assert isinstance(m, CriticMetrics)
        # 픽스처에 REPLAN 복구/미복구 케이스가 모두 있어 복구율이 0<r<1 이다.
        assert m.replan_traces >= 2
        assert m.replan_recovery_rate is not None
        assert 0.0 < m.replan_recovery_rate < 1.0
        # decision 3택이 모두 등장.
        assert set(m.decision_counts) >= {"ANSWER", "REPLAN", "STOP"}
        # 80% 경로 보존(미진입 RETRIEVE) 존재.
        assert m.path_preserved >= 1
        # 타이밍 있는 픽스처가 있어 critic 스팬 소요 표본이 잡힌다.
        assert m.critic_latency_samples >= 1

    def test_report_renders(self):
        report = format_report(compute_metrics(load_fixture_critic_traces()))
        assert "critic 발동율" in report
        assert "REPLAN 복구율" in report
        assert "80% 경로 보존" in report


# ── CLI 드라이런 end-to-end ────────────────────────────────────────────────


class TestCliDryRun:
    def test_dry_run_cli_end_to_end(self):
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.eval.l1.run_critic_metrics", "--dry-run"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "critic 픽스처" in proc.stdout
        assert "critic 발동율" in proc.stdout
