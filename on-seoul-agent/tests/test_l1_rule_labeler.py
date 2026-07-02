"""규칙 자동 라벨러 단위 테스트 — state 신호만으로 결정적 라벨(LLM 불필요)."""

from scripts.eval.l1.rule_labeler import label_rule
from scripts.eval.l1.signals import QuerySignals, RuleBucket


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
        # NORMAL = 검색은 됐고(action=RETRIEVE) 실패 신호 없음.
        assert label_rule(_sig(action="RETRIEVE", total_hits=5)) is RuleBucket.NORMAL

    def test_unknown_hits_defaults_normal(self):
        # 건수 신호가 전혀 없으면(구 트레이스) 실패로 단정하지 않고 NORMAL.
        # action 도 None(구 트레이스) → 검색 미시도로 단정하지 않음(하위호환).
        assert label_rule(_sig()) is RuleBucket.NORMAL


class TestNonRetrieveScoping:
    """검색 미시도(action≠RETRIEVE 또는 META)는 NON_RETRIEVE 로 분리 — 분모 제외."""

    def test_direct_answer_is_non_retrieve(self):
        assert label_rule(_sig(action="DIRECT_ANSWER")) is RuleBucket.NON_RETRIEVE

    def test_out_of_scope_is_non_retrieve(self):
        assert label_rule(_sig(action="OUT_OF_SCOPE")) is RuleBucket.NON_RETRIEVE

    def test_ambiguous_is_non_retrieve(self):
        assert label_rule(_sig(action="AMBIGUOUS")) is RuleBucket.NON_RETRIEVE

    def test_meta_turn_is_non_retrieve_even_if_action_retrieve(self):
        # META 턴은 action=RETRIEVE 여도 검색 실패가 아니라 설명 턴 → 분모 제외.
        s = _sig(action="RETRIEVE", turn_kind="META")
        assert label_rule(s) is RuleBucket.NON_RETRIEVE

    def test_non_retrieve_beats_zero_hit(self):
        # 검색 미시도 판정이 최우선 — 0건 신호가 있어도(우연히 담겼어도) NON_RETRIEVE.
        s = _sig(action="DIRECT_ANSWER", total_hits=0)
        assert label_rule(s) is RuleBucket.NON_RETRIEVE

    def test_retrieve_new_turn_not_scoped_out(self):
        # action=RETRIEVE + turn_kind=NEW 는 정상 검색 트레이스(NON_RETRIEVE 아님).
        s = _sig(action="RETRIEVE", turn_kind="NEW", total_hits=5)
        assert label_rule(s) is RuleBucket.NORMAL

    def test_drill_retrieve_is_retrieval_not_scoped_out(self):
        # DRILL(멀티홉 후속)은 검색을 시도하므로 분모에 남는다(META 만 제외).
        s = _sig(action="RETRIEVE", turn_kind="DRILL", total_hits=1)
        assert label_rule(s) is RuleBucket.NORMAL

    def test_old_trace_no_action_not_scoped_out(self):
        # 구 트레이스(action None) 는 검색 미시도로 단정하지 않는다(하위호환).
        s = _sig(total_hits=5)
        assert label_rule(s) is not RuleBucket.NON_RETRIEVE
