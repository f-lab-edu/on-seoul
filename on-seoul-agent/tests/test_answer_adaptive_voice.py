"""P3 적응형 답변 조립 + §2.5 보이스 단위 테스트.

_build_card_system 에 result_quality/reservation_guide_shown 를 얹은 뒤:
- 쏠림 → REFINE_HINT 미포함 + SKEW_OFFER 포함
- reservation_guide_shown=True → RESERVATION_GUIDE 생략
- thin → THIN_CAVEAT
- 플래그 없음 → 현행 출력 동일(회귀 가드)
- 거절/캐비엇 경로 system 프롬프트에 §2.5 공통 보이스 지침 포함(보이스 가드)
"""

from agents.answer_agent import (
    _CLAUSE_REFINE_HINT,
    _CLAUSE_RESERVATION_GUIDE,
    _CLAUSE_SKEW_OFFER,
    _CLAUSE_THIN_CAVEAT,
    _VOICE_GUIDE,
    _build_card_system,
)


def _rows(areas, status="예약마감"):
    return [
        {"service_id": f"P{i}", "area_name": a, "service_status": status}
        for i, a in enumerate(areas)
    ]


class TestSkewOffer:
    def test_skew_replaces_refine_hint(self):
        """쏠림(area_name) → REFINE_HINT 미포함 + SKEW_OFFER(치환본) 포함."""
        rq = {"skew_field": "area_name", "skew_value": "강남구", "thin": False}
        prompt = _build_card_system(
            "지금 접수중 체육시설", _rows(["강남구"] * 5), None, result_quality=rq
        )
        assert _CLAUSE_REFINE_HINT not in prompt
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") in prompt
        assert "강남구" in prompt

    def test_skew_value_interpolated(self):
        rq = {"skew_field": "area_name", "skew_value": "마포구", "thin": False}
        prompt = _build_card_system(
            "체육시설", _rows(["마포구"] * 4), None, result_quality=rq
        )
        assert "마포구" in prompt

    def test_skew_value_none_interpolates_empty(self):
        """경계: skew_field=area_name 인데 skew_value=None → '' 치환(KeyError/None 미노출)."""
        rq = {"skew_field": "area_name", "skew_value": None, "thin": False}
        prompt = _build_card_system(
            "체육시설", _rows(["강남구"] * 4), None, result_quality=rq
        )
        assert _CLAUSE_REFINE_HINT not in prompt
        assert "None" not in prompt
        # 치환 자리({skew_value})가 그대로 남지 않았는지(포맷 성공) 확인.
        assert "{skew_value}" not in prompt

    def test_resolved_area_name_still_no_refine_hint(self):
        """기존 area_name 해소 가드 유지 — 지역 해소 시 refine hint 없음."""
        prompt = _build_card_system(
            "그 중 무료만", _rows(["강남구"] * 2), "강남구"
        )
        assert _CLAUSE_REFINE_HINT not in prompt


class TestThinCaveat:
    def test_thin_adds_caveat(self):
        rq = {"skew_field": None, "skew_value": None, "thin": True}
        prompt = _build_card_system(
            "강남구 수영장", _rows(["강남구"]), "강남구", result_quality=rq
        )
        assert _CLAUSE_THIN_CAVEAT in prompt

    def test_thin_and_relaxed_merge_single(self):
        """완화(retry_relaxed)와 thin 캐비엇이 겹치면 thin 캐비엇은 생략(중복 방지)."""
        rq = {"skew_field": None, "skew_value": None, "thin": True}
        prompt = _build_card_system(
            "강남구 무료 수영장",
            _rows(["강남구"]),
            "강남구",
            retry_relaxed=True,
            relaxed_filters=["payment_type"],
            result_quality=rq,
        )
        assert "완화한 결과입니다" in prompt
        assert _CLAUSE_THIN_CAVEAT not in prompt

    def test_thin_flag_but_empty_results_no_caveat(self):
        """경계: thin=True 인데 results=[] → 'and results' 가드로 캐비엇 미추가.

        (0건은 본래 0건 게이트가 처리하므로 카드형 캐비엇 대상이 아니다.)
        """
        rq = {"skew_field": None, "skew_value": None, "thin": True}
        prompt = _build_card_system("수영장", [], "강남구", result_quality=rq)
        assert _CLAUSE_THIN_CAVEAT not in prompt

    def test_thin_and_skew_both_present(self):
        """겹침: thin + 쏠림 동시 → 둘 다 독립 추가(SKEW_OFFER + THIN_CAVEAT).

        완화 재시도가 아니므로 thin 캐비엇 생략 조건에 해당하지 않는다.
        """
        rq = {"skew_field": "area_name", "skew_value": "강남구", "thin": True}
        prompt = _build_card_system(
            "체육시설", _rows(["강남구"] * 2), None, result_quality=rq
        )
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") in prompt
        assert _CLAUSE_THIN_CAVEAT in prompt
        assert _CLAUSE_REFINE_HINT not in prompt


class TestReservationGuideSuppression:
    def test_shown_suppresses_guide(self):
        """reservation_guide_shown=True → RESERVATION_GUIDE 생략."""
        prompt = _build_card_system(
            "강남구 수영장",
            _rows(["강남구"] * 3, status="접수중"),
            "강남구",
            reservation_guide_shown=True,
        )
        assert _CLAUSE_RESERVATION_GUIDE not in prompt

    def test_first_exposure_keeps_guide(self):
        """첫 노출(False) → RESERVATION_GUIDE 유지."""
        prompt = _build_card_system(
            "강남구 수영장",
            _rows(["강남구"] * 3, status="접수중"),
            "강남구",
            reservation_guide_shown=False,
        )
        assert _CLAUSE_RESERVATION_GUIDE in prompt


class TestBackwardCompatRegression:
    def test_no_flags_matches_current_output(self):
        """플래그 없음(기본 인자) → 현행 출력과 동일(완전 하위호환)."""
        rows = _rows(["강남구", "마포구"], status="접수중")
        baseline = _build_card_system("수영장 알려줘", rows, None)
        with_defaults = _build_card_system(
            "수영장 알려줘",
            rows,
            None,
            result_quality=None,
            reservation_guide_shown=False,
        )
        assert baseline == with_defaults
        # 플래그 없으면 기존 REFINE_HINT 경로 그대로(지역 미지정).
        assert _CLAUSE_REFINE_HINT in baseline
        assert _CLAUSE_SKEW_OFFER not in baseline


class TestVoiceGuide:
    def test_card_system_includes_voice_guide(self):
        """카드형 system 프롬프트 상단에 공통 보이스 지침 포함."""
        prompt = _build_card_system("수영장", _rows(["강남구"]), None)
        assert _VOICE_GUIDE in prompt

    def test_voice_guide_present_under_skew(self):
        """캐비엇/제안 경로에서도 보이스 지침 유지(톤 회귀 가드)."""
        rq = {"skew_field": "area_name", "skew_value": "강남구", "thin": False}
        prompt = _build_card_system(
            "체육시설", _rows(["강남구"] * 5), None, result_quality=rq
        )
        assert _VOICE_GUIDE in prompt
