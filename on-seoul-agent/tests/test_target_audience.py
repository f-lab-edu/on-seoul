"""tools/target_audience.py — matches_audience / build_audience_sql 단위 테스트.

계획서 P1+P2 검증 포인트(비자명 로직):
  · 제한없음/가족 항상 통과
  · '성인'은 CHILD 에서 drop, ADULT/SENIOR 에서 통과
  · '유아'는 SENIOR 에서 drop, CHILD 에서 통과
  · 콤마 다중값("성인, 초등학생")은 CHILD·ADULT 양쪽 통과
"""

from tools.target_audience import (
    AUDIENCE_TOKENS,
    build_audience_sql,
    matches_audience,
)


class TestMatchesAudience:
    def test_always_pass_tokens(self):
        for group in AUDIENCE_TOKENS:
            assert matches_audience("제한없음", group) is True
            assert matches_audience("가족", group) is True

    def test_adult_dropped_in_child(self):
        assert matches_audience("성인", "CHILD") is False

    def test_adult_passes_in_adult_and_senior(self):
        assert matches_audience("성인", "ADULT") is True
        assert matches_audience("성인", "SENIOR") is True

    def test_infant_dropped_in_senior(self):
        assert matches_audience("유아", "SENIOR") is False

    def test_infant_passes_in_child(self):
        assert matches_audience("유아", "CHILD") is True

    def test_comma_multivalue_absorbed_both_sides(self):
        target = "성인, 초등학생"
        assert matches_audience(target, "CHILD") is True
        assert matches_audience(target, "ADULT") is True

    def test_comma_multivalue_senior_asymmetry(self):
        # "유아, 성인" 은 SENIOR 에서 성인 토큰으로 통과(유아 배제 비대칭 유지).
        assert matches_audience("유아, 성인", "SENIOR") is True
        # "유아, 어린이" 는 SENIOR 에서 drop(성인 토큰 없음).
        assert matches_audience("유아, 어린이", "SENIOR") is False

    def test_family_group_matches_only_family(self):
        assert matches_audience("가족", "FAMILY") is True
        # 가족은 always-pass 토큰이라 어떤 그룹에서도 통과 — CHILD 에서도 True.
        assert matches_audience("성인", "FAMILY") is False

    def test_none_group_always_passes(self):
        assert matches_audience("성인", None) is True
        assert matches_audience(None, None) is True

    def test_unknown_group_treated_as_no_filter(self):
        assert matches_audience("성인", "ROBOT") is True

    def test_empty_target_dropped_when_group_set(self):
        assert matches_audience(None, "CHILD") is False
        assert matches_audience("", "CHILD") is False


class TestBuildAudienceSql:
    def test_none_group_yields_no_predicate(self):
        sql, bind = build_audience_sql(None)
        assert sql is None
        assert bind == {}

    def test_child_predicate_is_parameterized(self):
        sql, bind = build_audience_sql("CHILD")
        assert sql is not None
        # 항상통과(제한없음/가족) + CHILD 토큰이 모두 bind 로 들어간다(값 삽입 금지).
        assert "target_info LIKE :aud_0" in sql
        assert all(v.startswith("%") and v.endswith("%") for v in bind.values())
        # CHILD 토큰 + always-pass 2개 = 8개.
        assert len(bind) == len(AUDIENCE_TOKENS["CHILD"]) + 2
        assert "%제한없음%" in bind.values()
        assert "%초등학생%" in bind.values()
