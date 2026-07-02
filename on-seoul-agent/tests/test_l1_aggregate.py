"""집계 + 사람 검증 하네스 단위 테스트."""

import csv
import json

from scripts.eval.l1.aggregate import (
    build_distribution,
    export_human_review,
    load_human_labels,
    sample_for_review,
)
from scripts.eval.l1.signals import (
    LabeledQuery,
    LlmBucket,
    QuerySignals,
    RuleBucket,
)


def _labeled(
    tid: str,
    rule: RuleBucket,
    llm: LlmBucket | None = None,
    human: str | None = None,
    turn_kind: str | None = None,
) -> LabeledQuery:
    return LabeledQuery(
        signals=QuerySignals(trace_id=tid, raw_query=f"q{tid}", turn_kind=turn_kind),
        rule_bucket=rule,
        llm_bucket=llm,
        llm_rationale="r" if llm else None,
        human_bucket=human,
    )


class TestDistribution:
    def test_counts_and_demand_split(self):
        items = [
            _labeled("1", RuleBucket.ZERO_HIT),
            _labeled("2", RuleBucket.THIN),
            _labeled("3", RuleBucket.NORMAL, LlmBucket.INTENT_MISPICK),
            _labeled("4", RuleBucket.NORMAL, LlmBucket.COMPOUND_UNEXPRESSIBLE),
            _labeled("5", RuleBucket.NORMAL, LlmBucket.COMPOUND_UNEXPRESSIBLE),
            _labeled("6", RuleBucket.NORMAL, LlmBucket.NORMAL),
        ]
        dist = build_distribution(items)
        assert dist.total == 6
        assert dist.retrieval_total == 6  # NON_RETRIEVE 없음
        assert dist.rule_counts["ZERO_HIT"] == 1
        assert dist.rule_counts["THIN"] == 1
        assert dist.llm_counts["INTENT_MISPICK"] == 1
        assert dist.llm_counts["COMPOUND_UNEXPRESSIBLE"] == 2
        # L1 수요: ZERO_HIT + THIN + INTENT_MISPICK = 3
        assert dist.l1_demand == 3
        # L2 수요: COMPOUND_UNEXPRESSIBLE = 2
        assert dist.l2_demand == 2


class TestNonRetrieveScoping:
    def test_non_retrieve_excluded_from_demand_denominator(self):
        items = [
            _labeled("1", RuleBucket.ZERO_HIT),  # L1
            _labeled("2", RuleBucket.NON_RETRIEVE),  # 분모 제외
            _labeled("3", RuleBucket.NON_RETRIEVE),  # 분모 제외
            _labeled("4", RuleBucket.NORMAL),  # RETRIEVE 정상
        ]
        dist = build_distribution(items)
        assert dist.total == 4
        assert dist.retrieval_total == 2  # ZERO_HIT + NORMAL
        assert dist.non_retrieve_total == 2
        # NON_RETRIEVE 는 rule_counts 로 투명하게 보이되 수요에서 빠진다.
        assert dist.rule_counts["NON_RETRIEVE"] == 2
        assert dist.l1_demand == 1  # ZERO_HIT 만
        assert dist.l2_demand == 0

    def test_non_retrieve_never_counted_as_l1_even_with_stray_llm(self):
        # 방어: NON_RETRIEVE 는 (설령 llm 라벨이 붙어도) 수요로 세지 않는다.
        items = [_labeled("1", RuleBucket.NON_RETRIEVE, LlmBucket.INTENT_MISPICK)]
        dist = build_distribution(items)
        assert dist.l1_demand == 0
        assert dist.retrieval_total == 0

    def test_turn_kind_segment_over_retrieve_only(self):
        items = [
            _labeled("1", RuleBucket.NORMAL, turn_kind="NEW"),
            _labeled("2", RuleBucket.NORMAL, turn_kind="DRILL"),
            _labeled("3", RuleBucket.THIN, turn_kind="REFINE"),
            # NON_RETRIEVE(META)는 turn_kind 세그먼트에도 안 들어간다(분모 밖).
            _labeled("4", RuleBucket.NON_RETRIEVE, turn_kind="META"),
        ]
        dist = build_distribution(items)
        assert dist.turn_kind_counts == {"NEW": 1, "DRILL": 1, "REFINE": 1}
        assert "META" not in dist.turn_kind_counts

    def test_empty_and_all_mismatch_agreement_edges(self):
        # 빈 표본: 분모 0 → 나눗셈 없이 None(사람 미검증).
        d0 = build_distribution([])
        assert d0.total == 0
        assert d0.human_agreement is None
        assert d0.human_sample_size is None
        # 전량 불일치: 일치율 0.0(가려짐 없이 명시적 0).
        items = [
            _labeled("1", RuleBucket.ZERO_HIT, human="NORMAL"),
            _labeled("2", RuleBucket.THIN, human="NORMAL"),
        ]
        d = build_distribution(items)
        assert d.human_sample_size == 2
        assert d.human_agreement == 0.0

    def test_agreement_computed_when_human_present(self):
        items = [
            _labeled("1", RuleBucket.ZERO_HIT, human="ZERO_HIT"),  # 일치
            _labeled("2", RuleBucket.THIN, human="NORMAL"),  # 불일치
            _labeled("3", RuleBucket.NORMAL, LlmBucket.DRIFT, human="DRIFT"),  # 일치
            _labeled("4", RuleBucket.NORMAL),  # 사람 미검증 → 제외
        ]
        dist = build_distribution(items)
        assert dist.human_sample_size == 3
        assert dist.human_agreement == 2 / 3


class TestReviewHarness:
    def test_sample_deterministic_and_capped(self):
        items = [_labeled(str(i), RuleBucket.NORMAL) for i in range(200)]
        s1 = sample_for_review(items, n=50, seed=7)
        s2 = sample_for_review(items, n=50, seed=7)
        assert len(s1) == 50
        assert [x.signals.trace_id for x in s1] == [x.signals.trace_id for x in s2]

    def test_sample_returns_all_when_fewer(self):
        items = [_labeled(str(i), RuleBucket.NORMAL) for i in range(10)]
        assert len(sample_for_review(items, n=50, seed=7)) == 10

    def test_export_and_load_roundtrip_csv(self, tmp_path):
        items = [
            _labeled("1", RuleBucket.ZERO_HIT, LlmBucket.INTENT_MISPICK),
            _labeled("2", RuleBucket.THIN),
        ]
        path = tmp_path / "review.csv"
        export_human_review(items, path)
        # CSV 에 자동 라벨 + 빈 human_bucket 열이 있어야 한다.
        with open(path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert "human_bucket" in rows[0]
        assert rows[0]["human_bucket"] == ""
        assert rows[0]["auto_bucket"]  # 자동 라벨 채워짐

        # 사람이 human_bucket 을 채웠다고 가정하고 로드.
        rows[0]["human_bucket"] = "ZERO_HIT"
        rows[1]["human_bucket"] = "NORMAL"
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader()
            w.writerows(rows)

        labels = load_human_labels(path)
        assert labels["1"] == "ZERO_HIT"
        assert labels["2"] == "NORMAL"

    def test_export_json(self, tmp_path):
        items = [_labeled("1", RuleBucket.ZERO_HIT)]
        path = tmp_path / "review.json"
        export_human_review(items, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[0]["trace_id"] == "1"
