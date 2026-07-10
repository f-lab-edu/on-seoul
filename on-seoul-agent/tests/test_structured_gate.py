"""agents/nodes/_shared.py::apply_structured_gate — post-RRF 구조화 게이트.

: 벡터의 summary/question/bm25 채널로 들어온 타 지역·상충 대상 행을
hydration 이후 원본 area_name/target_info 로 최종 교정한다(행23 강동구 누출 차단).
"""

from agents.nodes._shared import apply_structured_gate


def _row(
    sid: str, area: str, target: str = "제한없음", max_class: str = "체육시설"
) -> dict:
    return {
        "service_id": sid,
        "area_name": area,
        "target_info": target,
        "max_class_name": max_class,
    }


class TestApplyStructuredGate:
    def test_no_filters_passthrough(self):
        rows = [_row("A", "강서구"), _row("B", "강동구")]
        assert (
            apply_structured_gate(
                rows, area_names=None, max_class_names=None, target_audience=None
            )
            == rows
        )

    def test_area_gate_removes_other_district(self):
        """강서구 질의에 강동구 누출 행(행23) 제거."""
        rows = [_row("A", "강서구"), _row("LEAK", "강동구")]
        gated = apply_structured_gate(
            rows, area_names=["강서구"], max_class_names=None, target_audience=None
        )
        assert [r["service_id"] for r in gated] == ["A"]

    def test_area_gate_multi_region_keeps_both(self):
        rows = [_row("A", "성동구"), _row("B", "광진구"), _row("C", "마포구")]
        gated = apply_structured_gate(
            rows,
            area_names=["성동구", "광진구"],
            max_class_names=None,
            target_audience=None,
        )
        assert {r["service_id"] for r in gated} == {"A", "B"}

    def test_audience_gate_drops_conflicting_target(self):
        """어르신(SENIOR) 질의에 유아 전용 행 제거, 성인 행은 유지(행25)."""
        rows = [
            _row("SENIOR_OK", "강남구", "성인"),
            _row("INFANT_ONLY", "강남구", "유아"),
        ]
        gated = apply_structured_gate(
            rows, area_names=None, max_class_names=None, target_audience="SENIOR"
        )
        assert [r["service_id"] for r in gated] == ["SENIOR_OK"]

    def test_child_gate_keeps_always_pass(self):
        rows = [
            _row("R1", "강남구", "제한없음"),
            _row("R2", "강남구", "가족"),
            _row("ADULT", "강남구", "성인"),
        ]
        gated = apply_structured_gate(
            rows, area_names=None, max_class_names=None, target_audience="CHILD"
        )
        assert [r["service_id"] for r in gated] == ["R1", "R2"]

    def test_combined_area_and_audience(self):
        rows = [
            _row("KEEP", "강서구", "초등학생"),
            _row("BAD_AREA", "강동구", "초등학생"),
            _row("BAD_AUD", "강서구", "성인"),
        ]
        gated = apply_structured_gate(
            rows, area_names=["강서구"], max_class_names=None, target_audience="CHILD"
        )
        assert [r["service_id"] for r in gated] == ["KEEP"]

    def test_max_class_gate_removes_off_category(self):
        """체육시설 질의에 타 카테고리 누출 행(bm25 채널) 제거."""
        rows = [
            _row("SPORT", "강남구", max_class="체육시설"),
            _row("LEAK", "강남구", max_class="문화행사"),
        ]
        gated = apply_structured_gate(
            rows,
            area_names=None,
            max_class_names=["체육시설"],
            target_audience=None,
        )
        assert [r["service_id"] for r in gated] == ["SPORT"]

    def test_max_class_gate_complement_excludes_only_named(self):
        """"체육시설 말고"→여집합 리스트가 체육시설만 제외하고 나머지는 통과."""
        complement = ["문화행사", "시설대관", "교육", "진료"]
        rows = [
            _row("CULTURE", "강남구", max_class="문화행사"),
            _row("SPORT_OUT", "강남구", max_class="체육시설"),
            _row("EDU", "강남구", max_class="교육"),
        ]
        gated = apply_structured_gate(
            rows,
            area_names=None,
            max_class_names=complement,
            target_audience=None,
        )
        assert [r["service_id"] for r in gated] == ["CULTURE", "EDU"]

    def test_max_class_scalar_string_not_char_split(self):
        """스칼라 문자열 오주입 시 char-set 으로 쪼개져 오필터되지 않는다(area 대칭)."""
        rows = [
            _row("SPORT", "강남구", max_class="체육시설"),
            _row("CULTURE", "강남구", max_class="문화행사"),
        ]
        gated = apply_structured_gate(
            rows,
            area_names=None,
            max_class_names="체육시설",  # type: ignore[arg-type]
            target_audience=None,
        )
        assert [r["service_id"] for r in gated] == ["SPORT"]
