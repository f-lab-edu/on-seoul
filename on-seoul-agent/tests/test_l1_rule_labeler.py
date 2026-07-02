"""규칙 자동 라벨러 단위 테스트 — state 신호만으로 결정적 라벨(LLM 불필요)."""

from scripts.l1_eval.rule_labeler import label_rule
from scripts.l1_eval.signals import QuerySignals, RuleBucket


def _sig(**kw) -> QuerySignals:
    base = {"trace_id": "t1", "raw_query": "테스트 질의"}
    base.update(kw)
    return QuerySignals(**base)


class TestRuleLabeler:
    def test_zero_hit_from_total(self):
        assert label_rule(_sig(total_hits=0)) is RuleBucket.ZERO_HIT

    def test_zero_hit_from_channel_sum(self):
        assert label_rule(_sig(sql_hits=0, vector_hits=0)) is RuleBucket.ZERO_HIT

    def test_thin(self):
        s = _sig(total_hits=2, result_quality={"thin": True})
        assert label_rule(s) is RuleBucket.THIN

    def test_skew(self):
        s = _sig(
            total_hits=5,
            result_quality={"skew_field": "area_name", "skew_ratio": 0.9},
        )
        assert label_rule(s) is RuleBucket.SKEW

    def test_zero_hit_takes_priority_over_thin(self):
        # 0건이면 thin 신호가 있어도 ZERO_HIT 이 우선(더 강한 실패).
        s = _sig(total_hits=0, result_quality={"thin": True})
        assert label_rule(s) is RuleBucket.ZERO_HIT

    def test_retried_when_no_quality_signal(self):
        s = _sig(total_hits=5, retry_count=1)
        assert label_rule(s) is RuleBucket.RETRIED

    def test_retried_from_forced_intent(self):
        s = _sig(total_hits=5, forced_intent="VECTOR_SEARCH")
        assert label_rule(s) is RuleBucket.RETRIED

    def test_thin_takes_priority_over_retried(self):
        # 재시도 후에도 여전히 thin 이면 품질 실패(THIN)로 라벨.
        s = _sig(total_hits=2, retry_count=1, result_quality={"thin": True})
        assert label_rule(s) is RuleBucket.THIN

    def test_normal(self):
        assert label_rule(_sig(total_hits=5)) is RuleBucket.NORMAL

    def test_unknown_hits_defaults_normal(self):
        # 건수 신호가 전혀 없으면(구 트레이스) 실패로 단정하지 않고 NORMAL.
        assert label_rule(_sig()) is RuleBucket.NORMAL
