"""
병렬 비교 eval 스크립트의 순수 로직(집계/speedup/set 일치/순서 swap/percentile)을
DB·LLM 없이 검증한다. retrieval 자체(채널·세션)는 test_vector_parallel_search.py가
담당하므로 여기서는 측정 공정성·집계 정확성만 다룬다.
"""

import argparse

import pytest
from scripts.eval import compare_parallel as cp
from scripts.eval.run_recall import EvalRow


# ---------------------------------------------------------------------------
# _pct — nearest-rank 백분위
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty_returns_zero(self):
        assert cp._pct([], 50) == 0.0
        assert cp._pct([], 95) == 0.0

    def test_single_value(self):
        assert cp._pct([42.0], 50) == 42.0
        assert cp._pct([42.0], 95) == 42.0

    def test_p50_median_like(self):
        # 5개 정렬 [10,20,30,40,50]; p50 -> rank round(0.5*4)=2 -> 30
        assert cp._pct([50.0, 10.0, 30.0, 20.0, 40.0], 50) == 30.0

    def test_p95_near_max(self):
        vals = [float(x) for x in range(1, 101)]  # 1..100
        # rank round(0.95*99)=94 -> ordered[94]=95
        assert cp._pct(vals, 95) == 95.0

    def test_p0_and_p100_clamped(self):
        vals = [3.0, 1.0, 2.0]
        assert cp._pct(vals, 0) == 1.0
        assert cp._pct(vals, 100) == 3.0


# ---------------------------------------------------------------------------
# _fuse — RRF 결합 결과가 순차/병렬에서 동일 입력이면 동일 출력
# ---------------------------------------------------------------------------


class TestFuse:
    def test_fuse_returns_topk_service_ids(self):
        a = [{"service_id": "S1"}, {"service_id": "S2"}]
        b = [{"service_id": "S1"}, {"service_id": "S3"}]
        c: list[dict] = []
        d: list[dict] = []
        ids = cp._fuse(a, b, c, d, weights=None)
        assert isinstance(ids, list)
        assert all(isinstance(s, str) for s in ids)
        # S1이 두 채널에서 모두 상위 -> 1순위
        assert ids[0] == "S1"

    def test_fuse_same_input_same_output(self):
        a = [{"service_id": "A"}, {"service_id": "B"}]
        b = [{"service_id": "B"}, {"service_id": "C"}]
        c = [{"service_id": "A"}]
        d = [{"service_id": "D"}]
        first = cp._fuse(a, b, c, d, weights=None)
        second = cp._fuse(a, b, c, d, weights=None)
        assert first == second


# ---------------------------------------------------------------------------
# _quality_aggregate — recall@k·MRR 평균 집계
# ---------------------------------------------------------------------------


def _qr(query, seq_ids, par_ids):
    return cp._QualityRow(query=query, seq_ids=seq_ids, par_ids=par_ids)


class TestQualityAggregate:
    def test_empty_rows_zero(self):
        recall, mrr = cp._quality_aggregate([], {}, use_seq=True)
        assert mrr == 0.0
        assert recall == {1: 0.0, 5: 0.0, 10: 0.0}

    def test_perfect_recall_and_mrr(self):
        rows = [_qr("q1", ["S1", "S9"], ["S1", "S9"])]
        correct = {"q1": {"S1"}}
        recall, mrr = cp._quality_aggregate(rows, correct, use_seq=True)
        assert recall[1] == 1.0
        assert recall[5] == 1.0
        assert recall[10] == 1.0
        assert mrr == 1.0  # rank 1

    def test_recall_at_1_miss_but_at_5_hit(self):
        rows = [_qr("q1", ["X", "Y", "S1"], ["X", "Y", "S1"])]
        correct = {"q1": {"S1"}}
        recall, mrr = cp._quality_aggregate(rows, correct, use_seq=True)
        assert recall[1] == 0.0
        assert recall[5] == 1.0
        assert mrr == pytest.approx(1.0 / 3)

    def test_use_seq_vs_par_selects_correct_list(self):
        rows = [_qr("q1", ["S1"], ["X"])]
        correct = {"q1": {"S1"}}
        seq_recall, seq_mrr = cp._quality_aggregate(rows, correct, use_seq=True)
        par_recall, par_mrr = cp._quality_aggregate(rows, correct, use_seq=False)
        assert seq_recall[1] == 1.0 and seq_mrr == 1.0
        assert par_recall[1] == 0.0 and par_mrr == 0.0

    def test_average_across_queries(self):
        rows = [
            _qr("q1", ["S1"], ["S1"]),  # hit
            _qr("q2", ["X"], ["X"]),  # miss
        ]
        correct = {"q1": {"S1"}, "q2": {"S2"}}
        recall, mrr = cp._quality_aggregate(rows, correct, use_seq=True)
        assert recall[1] == 0.5
        assert mrr == 0.5

    def test_missing_correct_ids_treated_as_empty(self):
        rows = [_qr("q1", ["S1"], ["S1"])]
        recall, mrr = cp._quality_aggregate(rows, {}, use_seq=True)
        assert recall[1] == 0.0
        assert mrr == 0.0


# ---------------------------------------------------------------------------
# _build_result — set 일치 카운트 / speedup / recall delta 구조
# ---------------------------------------------------------------------------


def _args(reps=5):
    return argparse.Namespace(reps=reps, output="x.json", limit=None, sema_cap=None)


def _timing(query, seq_med, par_med):
    return cp._QueryTiming(
        query=query,
        seq_median_ms=seq_med,
        par_median_ms=par_med,
        seq_samples_ms=[seq_med],
        par_samples_ms=[par_med],
    )


class TestBuildResult:
    def test_set_equivalence_order_insensitive_match(self):
        # 동일 집합이지만 순서가 다른 경우 -> 일치로 카운트(RRF 동점 순서 흡수)
        rows = [_qr("q1", ["S1", "S2"], ["S2", "S1"])]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing("q1", 10.0, 5.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}},
        )
        assert res["set_equivalence"]["match"] == 1
        assert res["set_equivalence"]["mismatch"] == 0

    def test_set_equivalence_mismatch_recorded(self):
        rows = [_qr("q1", ["S1", "S2"], ["S1", "S3"])]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing("q1", 10.0, 5.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}},
        )
        assert res["set_equivalence"]["match"] == 0
        assert res["set_equivalence"]["mismatch"] == 1
        ex = res["set_equivalence"]["mismatch_examples"][0]
        assert ex["query"] == "q1"
        assert ex["seq_ids"] == ["S1", "S2"]
        assert ex["par_ids"] == ["S1", "S3"]

    def test_speedup_seq_over_par(self):
        # 순차 median 20, 병렬 median 5 -> speedup 4.0
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing("q1", 20.0, 5.0)],
            quality_rows=[_qr("q1", ["S1"], ["S1"])],
            correct_by_query={"q1": {"S1"}},
        )
        assert res["latency"]["speedup"] == 4.0
        assert res["latency"]["seq_median_ms"] == 20.0
        assert res["latency"]["par_median_ms"] == 5.0

    def test_speedup_zero_when_par_zero(self):
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing("q1", 20.0, 0.0)],
            quality_rows=[_qr("q1", ["S1"], ["S1"])],
            correct_by_query={"q1": {"S1"}},
        )
        assert res["latency"]["speedup"] == 0.0

    def test_recall_and_mrr_delta_zero_when_identical(self):
        # 동등성 검증: 순차/병렬 결과가 같으면 delta는 모두 0이어야 한다.
        rows = [
            _qr("q1", ["S1", "X"], ["S1", "X"]),
            _qr("q2", ["S2"], ["S2"]),
        ]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing("q1", 10.0, 5.0), _timing("q2", 8.0, 4.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}, "q2": {"S2"}},
        )
        for k in (1, 5, 10):
            assert res["quality"]["recall_delta"][f"recall@{k}"] == 0.0
        assert res["quality"]["mrr_delta"] == 0.0

    def test_metadata_fields_present(self):
        res = cp._build_result(
            args=_args(reps=7),
            sema_cap=12,
            weights={"track_a": 1.0},
            holdout_path="hold.tsv",
            timings=[_timing("q1", 10.0, 5.0)],
            quality_rows=[_qr("q1", ["S1"], ["S1"])],
            correct_by_query={"q1": {"S1"}},
        )
        assert res["reps"] == 7
        assert res["sema_cap"] == 12
        assert res["total_queries"] == 1
        assert res["weights"] == {"track_a": 1.0}
        assert "app global pool" in res["engine"]
        assert res["settings_snapshot"]["rrf_top_k_final"] >= 1


# ---------------------------------------------------------------------------
# _measure_query — 워밍업 폐기 + 반복별 순서 swap 검증 (DB 없이 fake retrieval)
# ---------------------------------------------------------------------------


def _prepared():
    row = EvalRow(query="q", intent="vector", sub_intent="", correct_ids=["S1"])
    return cp._PreparedQuery(
        row=row,
        refined_query="q",
        query_vector=[0.0],
        bm25_tokens=[],
        max_class_name=None,
        area_name=None,
        service_status=None,
        weights=None,
    )


class TestMeasureQueryFairness:
    @pytest.mark.asyncio
    async def test_warmup_discarded_and_swap_order(self, monkeypatch):
        """워밍업 1회씩 폐기 + 반복별 순서 swap을 호출 순서로 검증한다.

        reps=2 기준 기대 호출 순서:
          warmup: seq, par
          i=0 (짝수): seq, par
          i=1 (홀수): par, seq
        -> 전체 호출열: [seq,par, seq,par, par,seq]
        샘플 수는 seq/par 각각 reps(=2)건이어야 한다(워밍업 제외).
        """
        calls: list[str] = []

        async def fake_seq(pq):
            calls.append("seq")
            return ["S1"]

        async def fake_par(pq):
            calls.append("par")
            return ["S1"]

        monkeypatch.setattr(cp, "_retrieve_sequential", fake_seq)
        monkeypatch.setattr(cp, "_retrieve_parallel", fake_par)

        timing = await cp._measure_query(_prepared(), reps=2)

        assert calls == ["seq", "par", "seq", "par", "par", "seq"]
        # 워밍업 호출은 샘플에 포함되지 않는다 -> 각 reps건.
        assert len(timing.seq_samples_ms) == 2
        assert len(timing.par_samples_ms) == 2

    @pytest.mark.asyncio
    async def test_samples_routed_to_correct_list_regardless_of_order(
        self, monkeypatch
    ):
        """순서 swap이 있어도 seq 측정은 seq_samples, par 측정은 par_samples로만 들어간다."""

        async def fake_seq(pq):
            return ["S1"]

        async def fake_par(pq):
            return ["S1"]

        monkeypatch.setattr(cp, "_retrieve_sequential", fake_seq)
        monkeypatch.setattr(cp, "_retrieve_parallel", fake_par)

        timing = await cp._measure_query(_prepared(), reps=3)
        assert len(timing.seq_samples_ms) == 3
        assert len(timing.par_samples_ms) == 3
        assert timing.seq_median_ms >= 0.0
        assert timing.par_median_ms >= 0.0
