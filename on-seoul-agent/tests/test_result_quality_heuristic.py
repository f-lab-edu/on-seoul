"""P2 자각 패스(B) 휴리스틱 단위 테스트.

assess_result_quality(쏠림/빈약) + reservation_guide_already_shown(상류 history 파싱)
의 결정적 동작을 검증한다. 실제 LLM·DB 미호출.
"""

from agents._helpers import assess_result_quality, reservation_guide_already_shown


def _rows(areas: list[str]) -> list[dict]:
    return [{"service_id": f"P{i}", "area_name": a} for i, a in enumerate(areas)]


class TestAssessResultQualitySkew:
    def test_all_same_area_n5_skew_ratio_1(self):
        """5/5 동일 area → skew_field=area_name, skew_ratio=1.0."""
        rq = assess_result_quality(_rows(["강남구"] * 5), area_filter=None)
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["skew_value"] == "강남구"
        assert rq["skew_ratio"] == 1.0
        assert rq["thin"] is False

    def test_mixed_areas_no_skew(self):
        """혼합(임계 미만) → 쏠림 None (thin도 아님이면 result_quality=None)."""
        rq = assess_result_quality(
            _rows(["강남구", "마포구", "송파구", "종로구", "강서구"]),
            area_filter=None,
        )
        assert rq is None

    def test_below_threshold_no_skew(self):
        """3/5 강남(0.6 < 0.8 임계) → 쏠림 아님 → None."""
        rq = assess_result_quality(
            _rows(["강남구", "강남구", "강남구", "마포구", "송파구"]),
            area_filter=None,
        )
        assert rq is None

    def test_skew_suppressed_when_area_filter_set(self):
        """사용자가 지역 명시(area_filter 채워짐) → 쏠림 억제(정상)."""
        rq = assess_result_quality(_rows(["강남구"] * 5), area_filter="강남구")
        assert rq is None

    def test_skew_not_evaluated_below_min_count(self):
        """N<3 이면 쏠림 평가 안 함(1~2건 오판 방지). 2건은 thin 으로만 잡힌다."""
        rq = assess_result_quality(_rows(["강남구", "강남구"]), area_filter=None)
        assert rq is not None
        assert rq["thin"] is True
        assert rq.get("skew_field") is None

    def test_exactly_min_count_n3_all_same_skew(self):
        """경계: 정확히 N=3 동일 area → 쏠림 평가 대상(>=3), ratio=1.0."""
        rq = assess_result_quality(_rows(["강남구"] * 3), area_filter=None)
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["skew_ratio"] == 1.0
        assert rq["thin"] is False

    def test_ratio_exactly_at_threshold_is_skew(self):
        """경계: 4/5=0.8(임계와 정확히 같음) → 쏠림(>= 비교)."""
        rq = assess_result_quality(
            _rows(["강남구", "강남구", "강남구", "강남구", "마포구"]),
            area_filter=None,
        )
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["skew_value"] == "강남구"
        assert rq["skew_ratio"] == 0.8

    def test_ratio_just_below_threshold_no_skew(self):
        """경계: 5/7≈0.714(<0.8) → 쏠림 아님 → None(thin도 아님)."""
        rq = assess_result_quality(
            _rows(["강남구"] * 5 + ["마포구", "송파구"]),
            area_filter=None,
        )
        assert rq is None

    def test_all_area_name_none_no_skew(self):
        """적대적: area_name 전부 None(N>=3) → areas 비어 쏠림 평가 안 함 → None."""
        rows = [{"service_id": f"P{i}", "area_name": None} for i in range(5)]
        assert assess_result_quality(rows, area_filter=None) is None

    def test_missing_area_name_key_no_skew(self):
        """적대적: area_name 키 자체 부재(N>=3) → 쏠림 없음 → None."""
        rows = [{"service_id": f"P{i}"} for i in range(5)]
        assert assess_result_quality(rows, area_filter=None) is None

    def test_partial_none_denominator_is_full_n_skew(self):
        """경계: 4 강남 + 1 None(N=5) → ratio=4/5=0.8(분모는 None 포함 전체 N) → 쏠림."""
        rows = _rows(["강남구"] * 4) + [{"service_id": "Pn", "area_name": None}]
        rq = assess_result_quality(rows, area_filter=None)
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["skew_value"] == "강남구"
        assert rq["skew_ratio"] == 0.8

    def test_partial_none_dilutes_below_threshold_no_skew(self):
        """경계: 4 강남 + 2 None(N=6) → 4/6≈0.667(분모 전체 N) → 쏠림 아님 → None."""
        rows = _rows(["강남구"] * 4) + [
            {"service_id": "Pa", "area_name": None},
            {"service_id": "Pb", "area_name": None},
        ]
        assert assess_result_quality(rows, area_filter=None) is None

    def test_empty_string_area_filter_does_not_suppress(self):
        """경계: area_filter="" (falsy) 는 미지정과 동일 — 쏠림 억제 안 함."""
        rq = assess_result_quality(_rows(["강남구"] * 5), area_filter="")
        assert rq is not None
        assert rq["skew_field"] == "area_name"


class TestAssessResultQualityThin:
    def test_one_result_thin(self):
        rq = assess_result_quality(_rows(["강남구"]), area_filter=None)
        assert rq is not None
        assert rq["thin"] is True

    def test_two_results_thin(self):
        rq = assess_result_quality(_rows(["강남구", "마포구"]), area_filter=None)
        assert rq is not None
        assert rq["thin"] is True

    def test_three_results_not_thin(self):
        """3건은 thin 아님. 쏠림도 아니면 None."""
        rq = assess_result_quality(
            _rows(["강남구", "마포구", "송파구"]), area_filter=None
        )
        assert rq is None

    def test_skew_and_thin_can_be_independent(self):
        """N>=3 쏠림이면 skew 세팅, thin=False."""
        rq = assess_result_quality(_rows(["강남구"] * 4), area_filter=None)
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["thin"] is False


class TestAssessResultQualityIsolation:
    def test_zero_results_returns_none(self):
        """0건은 자각 패스 대상 아님(0건 게이트가 처리) → None."""
        assert assess_result_quality([], area_filter=None) is None


class TestReservationGuideAlreadyShown:
    def test_prev_assistant_with_guide_phrase_true(self):
        history = [
            {"role": "user", "content": "강남구 수영장"},
            {
                "role": "assistant",
                "content": "수영장 목록입니다. 시설예약 최초 이용자는 "
                "서울시 통합회원 가입이 필요합니다.",
            },
        ]
        assert reservation_guide_already_shown(history) is True

    def test_no_guide_phrase_false(self):
        history = [
            {"role": "user", "content": "강남구 수영장"},
            {"role": "assistant", "content": "수영장 목록입니다. 아래에서 확인해보세요."},
        ]
        assert reservation_guide_already_shown(history) is False

    def test_empty_history_false(self):
        assert reservation_guide_already_shown([]) is False
        assert reservation_guide_already_shown(None) is False

    def test_only_last_assistant_counts(self):
        """직전 assistant 발화만 본다(근사). user 발화의 우연 일치는 무시."""
        history = [
            {"role": "user", "content": "통합회원 가입이 필요한가요?"},
            {"role": "assistant", "content": "네, 아래에서 확인해보세요."},
        ]
        assert reservation_guide_already_shown(history) is False

    def test_history_without_any_assistant_false(self):
        """경계: assistant 발화가 전혀 없는 history → False(루프 끝 폴백)."""
        history = [
            {"role": "user", "content": "강남구 수영장"},
            {"role": "user", "content": "통합회원 가입"},
        ]
        assert reservation_guide_already_shown(history) is False

    def test_assistant_content_none_false(self):
        """경계: 직전 assistant content=None → (turn.get(content) or "") 폴백 → False."""
        history = [{"role": "assistant", "content": None}]
        assert reservation_guide_already_shown(history) is False

    def test_earlier_assistant_guide_ignored_when_last_lacks_it(self):
        """근사 정책: 더 과거 assistant 가 안내했어도 직전 발화만 본다 → False."""
        history = [
            {"role": "assistant", "content": "서울시 통합회원 가입이 필요합니다."},
            {"role": "user", "content": "다른 구도 알려줘"},
            {"role": "assistant", "content": "마포구 시설 목록입니다."},
        ]
        assert reservation_guide_already_shown(history) is False
