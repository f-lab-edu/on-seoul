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


def _qr(query, seq_ids, par_ids, union_ids=None):
    return cp._QualityRow(
        query=query,
        seq_ids=seq_ids,
        par_ids=par_ids,
        union_ids=union_ids if union_ids is not None else list(seq_ids),
    )


class TestQualityAggregate:
    def test_empty_rows_zero(self):
        recall, mrr = cp._quality_aggregate([], {}, path="seq")
        assert mrr == 0.0
        assert recall == {1: 0.0, 5: 0.0, 10: 0.0}

    def test_perfect_recall_and_mrr(self):
        rows = [_qr("q1", ["S1", "S9"], ["S1", "S9"])]
        correct = {"q1": {"S1"}}
        recall, mrr = cp._quality_aggregate(rows, correct, path="seq")
        assert recall[1] == 1.0
        assert recall[5] == 1.0
        assert recall[10] == 1.0
        assert mrr == 1.0  # rank 1

    def test_recall_at_1_miss_but_at_5_hit(self):
        rows = [_qr("q1", ["X", "Y", "S1"], ["X", "Y", "S1"])]
        correct = {"q1": {"S1"}}
        recall, mrr = cp._quality_aggregate(rows, correct, path="seq")
        assert recall[1] == 0.0
        assert recall[5] == 1.0
        assert mrr == pytest.approx(1.0 / 3)

    def test_path_selects_correct_list(self):
        rows = [_qr("q1", ["S1"], ["X"], ["Y"])]
        correct = {"q1": {"S1"}}
        seq_recall, seq_mrr = cp._quality_aggregate(rows, correct, path="seq")
        par_recall, par_mrr = cp._quality_aggregate(rows, correct, path="par")
        union_recall, union_mrr = cp._quality_aggregate(rows, correct, path="union")
        assert seq_recall[1] == 1.0 and seq_mrr == 1.0
        assert par_recall[1] == 0.0 and par_mrr == 0.0
        assert union_recall[1] == 0.0 and union_mrr == 0.0

    def test_average_across_queries(self):
        rows = [
            _qr("q1", ["S1"], ["S1"]),  # hit
            _qr("q2", ["X"], ["X"]),  # miss
        ]
        correct = {"q1": {"S1"}, "q2": {"S2"}}
        recall, mrr = cp._quality_aggregate(rows, correct, path="seq")
        assert recall[1] == 0.5
        assert mrr == 0.5

    def test_missing_correct_ids_treated_as_empty(self):
        rows = [_qr("q1", ["S1"], ["S1"])]
        recall, mrr = cp._quality_aggregate(rows, {}, path="seq")
        assert recall[1] == 0.0
        assert mrr == 0.0


# ---------------------------------------------------------------------------
# _build_result — set 일치 카운트 / speedup / recall delta 구조
# ---------------------------------------------------------------------------


def _args(reps=5):
    return argparse.Namespace(reps=reps, output="x.json", limit=None, sema_cap=None)


def _timing(query, seq_med, par_med, union_med=None):
    um = union_med if union_med is not None else par_med
    return cp._QueryTiming(
        query=query,
        seq_median_ms=seq_med,
        par_median_ms=par_med,
        union_median_ms=um,
        seq_samples_ms=[seq_med],
        par_samples_ms=[par_med],
        union_samples_ms=[um],
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
        assert res["latency"]["speedup_par"] == 4.0
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
        assert res["latency"]["speedup_par"] == 0.0

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
            assert res["quality"]["recall_delta_par"][f"recall@{k}"] == 0.0
            assert res["quality"]["recall_delta_union"][f"recall@{k}"] == 0.0
        assert res["quality"]["mrr_delta_par"] == 0.0
        assert res["quality"]["mrr_delta_union"] == 0.0

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
    async def test_warmup_discarded_and_rotated_order(self, monkeypatch):
        """워밍업 1회씩 폐기 + 반복별 3경로 순환(rotation)을 호출 순서로 검증한다.

        reps=3 기준 기대 호출 순서:
          warmup: seq, par, union (1회씩 폐기)
          i=0 (0회전): seq, par, union
          i=1 (1회전): par, union, seq
          i=2 (2회전): union, seq, par
        -> 측정 호출열: [seq,par,union, par,union,seq, union,seq,par]
        샘플 수는 각 경로 reps(=3)건이어야 한다(워밍업 제외).
        """
        calls: list[str] = []

        async def fake_seq(pq):
            calls.append("seq")
            return ["S1"]

        async def fake_par(pq):
            calls.append("par")
            return ["S1"]

        async def fake_union(pq):
            calls.append("union")
            return ["S1"]

        monkeypatch.setattr(cp, "_retrieve_sequential", fake_seq)
        monkeypatch.setattr(cp, "_retrieve_parallel", fake_par)
        monkeypatch.setattr(cp, "_retrieve_union", fake_union)

        timing = await cp._measure_query(_prepared(), reps=3)

        warmup = calls[:3]
        measured = calls[3:]
        assert sorted(warmup) == ["par", "seq", "union"]  # 워밍업 1회씩
        assert measured == [
            "seq", "par", "union",
            "par", "union", "seq",
            "union", "seq", "par",
        ]
        # 워밍업 호출은 샘플에 포함되지 않는다 -> 각 reps건.
        assert len(timing.seq_samples_ms) == 3
        assert len(timing.par_samples_ms) == 3
        assert len(timing.union_samples_ms) == 3

    @pytest.mark.asyncio
    async def test_samples_routed_to_correct_list_regardless_of_order(
        self, monkeypatch
    ):
        """순서 swap이 있어도 seq 측정은 seq_samples, par 측정은 par_samples로만 들어간다."""

        async def fake_seq(pq):
            return ["S1"]

        async def fake_par(pq):
            return ["S1"]

        async def fake_union(pq):
            return ["S1"]

        monkeypatch.setattr(cp, "_retrieve_sequential", fake_seq)
        monkeypatch.setattr(cp, "_retrieve_parallel", fake_par)
        monkeypatch.setattr(cp, "_retrieve_union", fake_union)

        timing = await cp._measure_query(_prepared(), reps=3)
        assert len(timing.seq_samples_ms) == 3
        assert len(timing.par_samples_ms) == 3
        assert len(timing.union_samples_ms) == 3
        assert timing.seq_median_ms >= 0.0
        assert timing.par_median_ms >= 0.0
        assert timing.union_median_ms >= 0.0


# ---------------------------------------------------------------------------
# 안 B(UNION ALL) 순수 헬퍼 — 채널 라벨 분류 / BM25 min-rank 머지 / _fuse 결합
# ---------------------------------------------------------------------------


def _urow(channel, rank, service_id):
    return {"channel": channel, "rank": rank, "service_id": service_id}


class TestClassifyUnionRows:
    def test_groups_by_channel_and_sorts_by_rank(self):
        rows = [
            _urow("track_a", 2, "S2"),
            _urow("track_a", 1, "S1"),
            _urow("track_b", 1, "S9"),
        ]
        out = cp._classify_union_rows(rows)
        assert out["track_a"] == ["S1", "S2"]  # rank 정렬
        assert out["track_b"] == ["S9"]

    def test_bm25_branches_kept_separate(self):
        rows = [
            _urow("bm25::service_name::0", 1, "S1"),
            _urow("bm25::metadata::0", 1, "S2"),
        ]
        out = cp._classify_union_rows(rows)
        assert out["bm25::service_name::0"] == ["S1"]
        assert out["bm25::metadata::0"] == ["S2"]

    def test_empty_rows_empty_dict(self):
        assert cp._classify_union_rows([]) == {}


class TestMergeUnionBm25:
    def test_min_rank_across_branches(self):
        # S1: service_name rank2, metadata rank1 -> min 1
        # S2: service_name rank1 -> min 1; tie-break service_id ASC
        union = {
            "bm25::service_name::0": ["S2", "S1"],  # S2 rank1, S1 rank2
            "bm25::metadata::0": ["S1"],  # S1 rank1
            "track_a": ["X"],  # 무시(bm25:: 아님)
        }
        merged = cp._merge_union_bm25(union)
        # S1 min-rank=1, S2 min-rank=1 -> (rank, sid) 정렬 -> S1, S2
        assert merged == ["S1", "S2"]

    def test_ignores_non_bm25_channels(self):
        union = {"track_a": ["A"], "track_b": ["B"]}
        assert cp._merge_union_bm25(union) == []


class TestFuseUnion:
    def test_fuse_union_matches_fuse_with_merged_bm25(self):
        # union 분류 결과 -> _fuse 와 동일 결합이어야 한다.
        union = {
            "track_a": ["S1", "S2"],
            "track_b": ["S1", "S3"],
            "track_c": [],
            "bm25::service_name::0": ["S4"],
        }
        ids = cp._fuse_union(union, weights=None)
        # 기존 _fuse 에 (a, b, c, merged_bm25) 를 직접 넣은 것과 동일해야 한다.
        a = [{"service_id": s} for s in ["S1", "S2"]]
        b = [{"service_id": s} for s in ["S1", "S3"]]
        c: list[dict] = []
        d = [{"service_id": s} for s in cp._merge_union_bm25(union)]
        assert ids == cp._fuse(a, b, c, d, weights=None)
        assert ids[0] == "S1"


# ---------------------------------------------------------------------------
# 3-way _build_result — seq/par/union 지연·품질·집합 일치
# ---------------------------------------------------------------------------


def _qr3(query, seq_ids, par_ids, union_ids):
    return cp._QualityRow(
        query=query, seq_ids=seq_ids, par_ids=par_ids, union_ids=union_ids
    )


def _timing3(query, seq_med, par_med, union_med):
    return cp._QueryTiming(
        query=query,
        seq_median_ms=seq_med,
        par_median_ms=par_med,
        union_median_ms=union_med,
        seq_samples_ms=[seq_med],
        par_samples_ms=[par_med],
        union_samples_ms=[union_med],
    )


class TestBuildResult3Way:
    def test_speedups_seq_over_par_and_union(self):
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing3("q1", 20.0, 5.0, 4.0)],
            quality_rows=[_qr3("q1", ["S1"], ["S1"], ["S1"])],
            correct_by_query={"q1": {"S1"}},
        )
        lat = res["latency"]
        assert lat["seq_median_ms"] == 20.0
        assert lat["par_median_ms"] == 5.0
        assert lat["union_median_ms"] == 4.0
        assert lat["speedup_par"] == 4.0  # 20/5
        assert lat["speedup_union"] == 5.0  # 20/4

    def test_three_way_set_equivalence_all_match(self):
        rows = [_qr3("q1", ["S1", "S2"], ["S2", "S1"], ["S1", "S2"])]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing3("q1", 10.0, 5.0, 4.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}},
        )
        assert res["set_equivalence"]["match"] == 1
        assert res["set_equivalence"]["mismatch"] == 0

    def test_three_way_set_equivalence_mismatch_records_all_three(self):
        rows = [_qr3("q1", ["S1"], ["S1"], ["S3"])]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing3("q1", 10.0, 5.0, 4.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}},
        )
        assert res["set_equivalence"]["mismatch"] == 1
        ex = res["set_equivalence"]["mismatch_examples"][0]
        assert ex["seq_ids"] == ["S1"]
        assert ex["par_ids"] == ["S1"]
        assert ex["union_ids"] == ["S3"]

    def test_quality_has_three_paths(self):
        rows = [_qr3("q1", ["S1"], ["S1"], ["S1"])]
        res = cp._build_result(
            args=_args(),
            sema_cap=4,
            weights=None,
            holdout_path="h.tsv",
            timings=[_timing3("q1", 10.0, 5.0, 4.0)],
            quality_rows=rows,
            correct_by_query={"q1": {"S1"}},
        )
        q = res["quality"]
        assert q["sequential"]["recall@1"] == 1.0
        assert q["parallel"]["recall@1"] == 1.0
        assert q["union"]["recall@1"] == 1.0
