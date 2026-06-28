"""단위 — `_curate_display` 결정적 카드 큐레이션(B-1).

순수 헬퍼: LLM/DB/추가검색 없이 in-memory 적합도 정렬만 한다.
적합도 키(area_name > max_class_name > payment_type > 예약가능 상태), 마감 강등,
동점 시 기존 순서 보존, 의도 제약 복원(비완화/완화), 딱맞음/대안 구분을 가드한다.
"""

from agents.answer_agent import _curate_display


def _row(sid, *, area=None, klass=None, pay=None, status=None):
    return {
        "service_id": sid,
        "area_name": area,
        "max_class_name": klass,
        "payment_type": pay,
        "service_status": status,
    }


class TestCurateSorting:
    def test_area_match_ranks_highest(self):
        rows = [
            _row("A", area="서초구", klass="체육시설"),
            _row("B", area="광진구", klass="문화행사"),
            _row("C", area="광진구", klass="체육시설"),
        ]
        intended = {"area_name": "광진구", "max_class_name": "체육시설"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        # 광진구+체육시설(딱맞음) > 광진구(타카테고리) > 서초구(타지역)
        assert [r["service_id"] for r in curated] == ["C", "B", "A"]

    def test_payment_free_preferred_when_requested(self):
        rows = [
            _row("A", area="광진구", klass="체육시설", pay="유료"),
            _row("B", area="광진구", klass="체육시설", pay="무료"),
        ]
        intended = {"area_name": "광진구", "max_class_name": "체육시설", "payment_type": "무료"}
        curated, _ = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["B", "A"]

    def test_closed_status_demoted_even_without_status_intent(self):
        """질의에 상태 미명시여도, 비완화에서도 마감(접수종료)은 강등된다(8-1)."""
        rows = [
            _row("CLOSED", area="광진구", klass="체육시설", status="접수종료"),
            _row("OPEN", area="광진구", klass="체육시설", status="접수중"),
        ]
        intended = {"area_name": "광진구", "max_class_name": "체육시설"}
        curated, _ = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["OPEN", "CLOSED"]

    def test_closed_demoted_but_not_excluded(self):
        """마감은 제외하지 않고 정렬-강등만 — 결과 개수 보존."""
        rows = [
            _row("X", area="광진구", status="예약마감"),
            _row("Y", area="광진구", status="접수종료"),
        ]
        intended = {"area_name": "광진구"}
        curated, _ = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert len(curated) == 2

    def test_status_order_open_gt_notice_gt_closed(self):
        rows = [
            _row("CLOSED", status="예약마감"),
            _row("NOTICE", status="안내중"),
            _row("OPEN", status="접수중"),
        ]
        curated, _ = _curate_display(rows, {}, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["OPEN", "NOTICE", "CLOSED"]

    def test_tie_preserves_original_order(self):
        """동점(모두 동일 적합도)은 기존 검색 순서를 보존한다(stable sort)."""
        rows = [
            _row("A", area="광진구", status="접수중"),
            _row("B", area="광진구", status="접수중"),
            _row("C", area="광진구", status="접수중"),
        ]
        intended = {"area_name": "광진구"}
        curated, _ = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["A", "B", "C"]


class TestAltCount:
    def test_all_exact_no_alt(self):
        rows = [
            _row("A", area="광진구", klass="체육시설", pay="무료", status="접수중"),
            _row("B", area="광진구", klass="체육시설", pay="무료", status="접수중"),
        ]
        intended = {"area_name": "광진구", "max_class_name": "체육시설", "payment_type": "무료"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert alt_count == 0

    def test_alternatives_counted_in_top5(self):
        """상위 5건 중 의도 제약을 모두 만족하지 못하는 항목 수 = alt_count."""
        rows = [
            _row("EXACT", area="광진구", klass="체육시설"),
            _row("OTHER_AREA", area="서초구", klass="체육시설"),
            _row("OTHER_CAT", area="광진구", klass="문화행사"),
        ]
        intended = {"area_name": "광진구", "max_class_name": "체육시설"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        # 광진구+체육시설 1건만 딱맞음, 나머지 2건은 대안
        assert curated[0]["service_id"] == "EXACT"
        assert alt_count == 2

    def test_no_intended_means_all_exact(self):
        """의도 제약이 없으면(intended 비어 있음) 전부 딱맞음 → alt_count=0."""
        rows = [_row("A"), _row("B")]
        curated, alt_count = _curate_display(rows, {}, relaxed=False, relaxed_filters=None)
        assert alt_count == 0


class TestPaymentVariantMatch:
    """payment_type 매칭이 sql_search 의미(유료=접두 매칭)와 정렬되는지 가드.

    DB distinct payment_type = {"무료","유료","유료(요금안내문의)"}. sql_search 는
    "유료" 요청을 LIKE '유료%' 접두 매칭으로 두 유료 변형을 모두 포섭한다. 큐레이션도
    동일 동치를 따라야 정당한 유료 결과를 "대안"으로 오라벨하지 않는다.
    """

    def test_paid_request_matches_paid_with_suffix(self):
        """유료 요청 + row "유료(요금안내문의)" → 적합(만족·딱맞음·대안 아님)."""
        rows = [
            _row("PLAIN", area="광진구", pay="유료"),
            _row("SUFFIX", area="광진구", pay="유료(요금안내문의)"),
        ]
        intended = {"area_name": "광진구", "payment_type": "유료"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        # 두 유료 변형 모두 만족 → 동점, 입력 순서 보존, 대안 0.
        assert [r["service_id"] for r in curated] == ["PLAIN", "SUFFIX"]
        assert alt_count == 0

    def test_paid_request_demotes_free(self):
        """유료 요청 + row "무료" → 불만족(강등·대안)."""
        rows = [
            _row("FREE", area="광진구", pay="무료"),
            _row("PAID", area="광진구", pay="유료(요금안내문의)"),
        ]
        intended = {"area_name": "광진구", "payment_type": "유료"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["PAID", "FREE"]
        assert alt_count == 1

    def test_free_request_matches_free(self):
        """무료 요청 + row "무료" → 적합."""
        rows = [_row("A", area="광진구", pay="무료")]
        intended = {"area_name": "광진구", "payment_type": "무료"}
        _, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert alt_count == 0

    def test_free_request_demotes_paid_variant(self):
        """무료 요청 + row "유료(요금안내문의)" → 불만족(정확 일치, 접두 아님)."""
        rows = [
            _row("PAID", area="광진구", pay="유료(요금안내문의)"),
            _row("FREE", area="광진구", pay="무료"),
        ]
        intended = {"area_name": "광진구", "payment_type": "무료"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        assert [r["service_id"] for r in curated] == ["FREE", "PAID"]
        assert alt_count == 1

    def test_no_payment_intent_neutral(self):
        """회귀: payment_type 의도 없음이면 payment 차원이 점수에 영향 없음."""
        rows = [
            _row("PAID", area="광진구", pay="유료(요금안내문의)"),
            _row("FREE", area="광진구", pay="무료"),
        ]
        intended = {"area_name": "광진구"}
        curated, alt_count = _curate_display(rows, intended, relaxed=False, relaxed_filters=None)
        # payment 무관 → area 만 만족, 동점, 입력 순서 보존, 대안 0.
        assert [r["service_id"] for r in curated] == ["PAID", "FREE"]
        assert alt_count == 0
