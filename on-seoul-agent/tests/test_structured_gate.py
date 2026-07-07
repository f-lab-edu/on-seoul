"""agents/nodes/_shared.py::apply_structured_gate — post-RRF 구조화 게이트.

계획서 P1+P2: 벡터의 summary/question/bm25 채널로 들어온 타 지역·상충 대상 행을
hydration 이후 원본 area_name/target_info 로 최종 교정한다(행23 강동구 누출 차단).
"""

from agents.nodes._shared import apply_structured_gate


def _row(sid: str, area: str, target: str = "제한없음") -> dict:
    return {"service_id": sid, "area_name": area, "target_info": target}


class TestApplyStructuredGate:
    def test_no_filters_passthrough(self):
        rows = [_row("A", "강서구"), _row("B", "강동구")]
        assert apply_structured_gate(rows, area_names=None, target_audience=None) == rows

    def test_area_gate_removes_other_district(self):
        """강서구 질의에 강동구 누출 행(행23) 제거."""
        rows = [_row("A", "강서구"), _row("LEAK", "강동구")]
        gated = apply_structured_gate(rows, area_names=["강서구"], target_audience=None)
        assert [r["service_id"] for r in gated] == ["A"]

    def test_area_gate_multi_region_keeps_both(self):
        rows = [_row("A", "성동구"), _row("B", "광진구"), _row("C", "마포구")]
        gated = apply_structured_gate(
            rows, area_names=["성동구", "광진구"], target_audience=None
        )
        assert {r["service_id"] for r in gated} == {"A", "B"}

    def test_audience_gate_drops_conflicting_target(self):
        """어르신(SENIOR) 질의에 유아 전용 행 제거, 성인 행은 유지(행25)."""
        rows = [
            _row("SENIOR_OK", "강남구", "성인"),
            _row("INFANT_ONLY", "강남구", "유아"),
        ]
        gated = apply_structured_gate(
            rows, area_names=None, target_audience="SENIOR"
        )
        assert [r["service_id"] for r in gated] == ["SENIOR_OK"]

    def test_child_gate_keeps_always_pass(self):
        rows = [
            _row("R1", "강남구", "제한없음"),
            _row("R2", "강남구", "가족"),
            _row("ADULT", "강남구", "성인"),
        ]
        gated = apply_structured_gate(
            rows, area_names=None, target_audience="CHILD"
        )
        assert [r["service_id"] for r in gated] == ["R1", "R2"]

    def test_combined_area_and_audience(self):
        rows = [
            _row("KEEP", "강서구", "초등학생"),
            _row("BAD_AREA", "강동구", "초등학생"),
            _row("BAD_AUD", "강서구", "성인"),
        ]
        gated = apply_structured_gate(
            rows, area_names=["강서구"], target_audience="CHILD"
        )
        assert [r["service_id"] for r in gated] == ["KEEP"]
