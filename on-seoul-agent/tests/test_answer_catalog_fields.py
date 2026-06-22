"""답변 가능 속성 카탈로그 확장 (결정 A) 테스트.

hydration 이 끌어오는 보유 정형 컬럼 중 use_time_start/end, cancel_std_type/days,
tel_no 를 _normalize 가 카드/LLM 컨텍스트로 노출하는지 검증한다.
service_open_*_dt 는 신뢰불가로 계속 제외한다.
"""

import datetime
import json

from agents.answer_agent import (
    AnswerAgent,
    _STRUCT_DETAIL,
    _guarded_use_time,
    _parse_time,
)
from schemas.state import IntentType
from tests.helpers import make_agent_state, make_answer_agent


class TestNormalizeNewFields:
    """_normalize 가 신규 신뢰 컬럼을 반환 dict 에 포함한다."""

    def _row(self):
        return {
            "service_id": "A1",
            "service_name": "마루공원 테니스장",
            "area_name": "강남구",
            "place_name": "마루공원",
            "service_url": "https://yeyak.seoul.go.kr/a1",
            "use_time_start": "09:00:00",
            "use_time_end": "18:00:00",
            "cancel_std_type": "이용일 기준",
            "cancel_std_days": 3,
            "tel_no": "02-1234-5678",
            # 신뢰불가 — 노출 금지.
            "service_open_start_dt": "2021-01-01T00:00:00",
            "service_open_end_dt": "2031-12-30T00:00:00",
        }

    def test_includes_use_time_cancel_std_tel(self):
        out = AnswerAgent._normalize(self._row())
        assert out["use_time_start"] == "09:00:00"
        assert out["use_time_end"] == "18:00:00"
        assert out["cancel_std_type"] == "이용일 기준"
        assert out["cancel_std_days"] == 3
        assert out["tel_no"] == "02-1234-5678"

    def test_excludes_service_open_dates(self):
        out = AnswerAgent._normalize(self._row())
        assert "service_open_start_dt" not in out
        assert "service_open_end_dt" not in out

    def test_missing_new_fields_default_none(self):
        """원본에 신규 컬럼이 없으면 None 으로 안전 처리(날조 금지)."""
        out = AnswerAgent._normalize({"service_id": "A1", "service_url": "https://x.io"})
        assert out["use_time_start"] is None
        assert out["cancel_std_type"] is None
        assert out["tel_no"] is None

    def test_use_time_time_object_serialized_via_iso(self):
        """use_time 이 실제 datetime.time(TIME 컬럼)으로 와도 _iso_or_none 으로
        isoformat 직렬화된다(DB async 드라이버는 raw str 이 아니라 time 객체를 줌).

        문자열 케이스만 검증하면 raw time 객체 직렬화 경로가 비커버로 남는다.
        """
        row = {
            "service_id": "A1",
            "service_url": "https://x.io",
            "use_time_start": datetime.time(6, 0, 0),
            "use_time_end": datetime.time(22, 30, 0),
        }
        out = AnswerAgent._normalize(row)
        assert out["use_time_start"] == "06:00:00"
        assert out["use_time_end"] == "22:30:00"
        # JSON 직렬화 가능(default=str 폴백 없이도) — 프론트 계약 안전.
        json.dumps(out, ensure_ascii=False)

    def test_cancel_std_days_smallint_passthrough_and_zero(self):
        """cancel_std_days(SMALLINT)는 int 그대로 통과하고 0 도 보존한다(falsy 누락 금지)."""
        out_zero = AnswerAgent._normalize(
            {"service_id": "A1", "service_url": "https://x.io", "cancel_std_days": 0}
        )
        assert out_zero["cancel_std_days"] == 0
        out_n = AnswerAgent._normalize(
            {"service_id": "A1", "service_url": "https://x.io", "cancel_std_days": 7}
        )
        assert out_n["cancel_std_days"] == 7


class TestUseTimeRenderGuard:
    """use_time 렌더 가드 — start>=end(placeholder/자정정규화 artifact) 둘 다 omit.

    실DB 검증: 미입력 00:00-00:00(399건) + 자정 정규화 artifact(08:00-00:00 =
    원래 08:00-24:00). 도메인상 자정넘김 운영창 전무라 start>=end 는 전부 오염.
    """

    def _row(self, start, end):
        return {
            "service_id": "A1",
            "service_url": "https://x.io",
            "use_time_start": start,
            "use_time_end": end,
        }

    def test_zero_placeholder_omitted(self):
        out = AnswerAgent._normalize(self._row("00:00:00", "00:00:00"))
        assert out["use_time_start"] is None
        assert out["use_time_end"] is None

    def test_midnight_normalize_artifact_omitted(self):
        # 08:00-00:00 = 원래 08:00-24:00 이 망가진 artifact. start>=end → omit.
        out = AnswerAgent._normalize(self._row("08:00:00", "00:00:00"))
        assert out["use_time_start"] is None
        assert out["use_time_end"] is None

    def test_start_after_end_omitted(self):
        out = AnswerAgent._normalize(self._row("18:00:00", "09:00:00"))
        assert out["use_time_start"] is None
        assert out["use_time_end"] is None

    def test_valid_window_survives_str(self):
        out = AnswerAgent._normalize(self._row("09:00:00", "18:00:00"))
        assert out["use_time_start"] == "09:00:00"
        assert out["use_time_end"] == "18:00:00"

    def test_valid_window_survives_time_object(self):
        out = AnswerAgent._normalize(
            self._row(datetime.time(10, 0, 0), datetime.time(11, 30, 0))
        )
        assert out["use_time_start"] == "10:00:00"
        assert out["use_time_end"] == "11:30:00"
        json.dumps(out, ensure_ascii=False)

    def test_one_side_missing_both_omitted(self):
        out = AnswerAgent._normalize(self._row("09:00:00", None))
        assert out["use_time_start"] is None
        assert out["use_time_end"] is None


class TestParseTimeSafety:
    """_parse_time 는 어떤 입력에도 throw 없이 datetime.time | None 만 반환한다.

    DB async 드라이버는 datetime.time 을, 일부 경로/테스트는 isoformat str 을
    줄 수 있고, 오염 경로로 잘못된 타입/문자열이 유입될 수 있다. 가드 비교가
    예외로 떨어지지 않도록 모든 입력을 안전 처리하는지 직접 검증한다.
    """

    def test_none_returns_none(self):
        assert _parse_time(None) is None

    def test_time_object_passthrough(self):
        t = datetime.time(9, 30, 0)
        assert _parse_time(t) == t

    def test_isoformat_str_hms(self):
        assert _parse_time("09:00:00") == datetime.time(9, 0, 0)

    def test_isoformat_str_hm(self):
        # HH:MM(초 없음)도 fromisoformat 가 처리한다.
        assert _parse_time("09:00") == datetime.time(9, 0)

    def test_garbage_str_returns_none_no_throw(self):
        assert _parse_time("garbage") is None

    def test_out_of_range_24h_returns_none(self):
        # 24:00:00 은 fromisoformat 가 거부 → None (throw 아님).
        assert _parse_time("24:00:00") is None

    def test_wrong_types_return_none_no_throw(self):
        # int/float/list/dict 등 비-str·비-time 타입도 str() 후 파싱 실패 → None.
        for bad in (123, 9.5, [], {}, ("09", "00")):
            assert _parse_time(bad) is None


class TestUseTimeZeroPadComparison:
    """zero-pad 안 된 값에서 사전식 str 비교 오판을 _parse_time 이 차단하는지.

    핵심 회귀: raw str 로 비교하면 "9:00" > "18:00" (사전식)이 True 가 되어 정상
    윈도를 placeholder 로 오판한다. 가드는 datetime.time 으로 파싱 후 비교하므로
    이 함정에 빠지지 않아야 한다. 단, fromisoformat 는 zero-pad 를 요구하므로
    non-zero-pad("9:00")는 파싱 불가로 (None, None) omit 된다(보수적 안전).
    """

    def test_non_zero_pad_not_lexically_misjudged(self):
        # 사전식이면 "9:00">"18:00" → start>=end 로 오판하지만, 실제로는 파싱
        # 불가라 (None, None). 어느 경로든 raw str 비교 오판은 발생하지 않는다.
        assert _guarded_use_time("9:00", "18:00") == (None, None)

    def test_zero_pad_valid_window_survives(self):
        # zero-pad 정상 윈도는 datetime.time 비교로 살아남는다.
        assert _guarded_use_time("09:00", "18:00") == ("09:00", "18:00")

    def test_time_object_comparison_not_lexical(self):
        # raw datetime.time 비교 — 9시<18시가 정확히 성립(사전식 아님).
        s, e = datetime.time(9, 0), datetime.time(18, 0)
        assert _guarded_use_time(s, e) == (s, e)


class TestTelNoRenderGuard:
    """tel_no 가드 — phone-shape 만 통과, 한글 등 garbage 는 omit.

    실DB 검증: present 중 11.3%(250건)에 한글 포함(부가설명/garbage). phone-shape
    정규식(^[0-9()+\\-,./\\s]+$) 매칭만 노출, 복수번호는 정상 통과.
    """

    def _row(self, tel):
        return {"service_id": "A1", "service_url": "https://x.io", "tel_no": tel}

    def test_plain_phone_passes(self):
        out = AnswerAgent._normalize(self._row("02-123-4567"))
        assert out["tel_no"] == "02-123-4567"

    def test_multi_number_passes(self):
        out = AnswerAgent._normalize(self._row("02-1,0232"))
        assert out["tel_no"] == "02-1,0232"

    def test_korean_garbage_omitted(self):
        out = AnswerAgent._normalize(self._row("02-123 담당자김"))
        assert out["tel_no"] is None

    def test_multi_number_with_space_passes(self):
        # 실DB 형태 복수번호("02-724-0200, 0232") — 콤마+공백 구분자가 허용 집합.
        out = AnswerAgent._normalize(self._row("02-724-0200, 0232"))
        assert out["tel_no"] == "02-724-0200, 0232"

    def test_none_passthrough(self):
        out = AnswerAgent._normalize(self._row(None))
        assert out["tel_no"] is None

    def test_empty_string_omitted(self):
        # 빈문자열은 정규식이 비매칭(1+ 글자 요구) → omit. None/빈문자 경계 보강.
        out = AnswerAgent._normalize(self._row(""))
        assert out["tel_no"] is None


class TestRenderGuardAppliesToBothPaths:
    """가드는 service_cards(프론트)와 results_json(LLM 컨텍스트) 양쪽에 동일 적용된다.

    두 출력은 동일한 _normalize(display) 결과에서 파생되므로, 오염값(omit 대상)이
    어느 한쪽에만 새거나 살아남지 않는지 회귀로 고정한다.
    """

    async def test_polluted_use_time_omitted_in_cards_and_json(self):
        # 08:00-00:00 자정 artifact + 한글 tel garbage 가 둘 다 omit 되는지.
        agent = make_answer_agent("안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "오염 시설",
                "place_name": "어딘가",
                "use_time_start": "08:00:00",
                "use_time_end": "00:00:00",
                "tel_no": "02-123 담당자김",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="identification",
            message="오염 시설 이용시간",
            hydrated_services=rows,
        )
        result = await agent.answer(state)

        displayed = json.loads(
            agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        )
        # LLM 컨텍스트(results_json): 오염값 omit.
        assert displayed[0]["use_time_start"] is None
        assert displayed[0]["use_time_end"] is None
        assert displayed[0]["tel_no"] is None
        # service_cards(프론트): 동일하게 omit.
        card = result["service_cards"][0]
        assert card["use_time_start"] is None
        assert card["use_time_end"] is None
        assert card["tel_no"] is None


class TestDetailPromptMentionsNewFields:
    """_STRUCT_DETAIL 이 이용시간·취소기준·문의처를 본문 서술하도록 지시한다."""

    def test_detail_prompt_mentions_use_time(self):
        assert "이용시간" in _STRUCT_DETAIL or "이용 시간" in _STRUCT_DETAIL

    def test_detail_prompt_mentions_cancel_std(self):
        assert "취소" in _STRUCT_DETAIL

    def test_detail_prompt_mentions_tel(self):
        assert "문의" in _STRUCT_DETAIL or "연락처" in _STRUCT_DETAIL

    def test_cancel_std_must_be_bundled(self):
        # cancel_std_type 단독 노출 금지: type+days 를 묶고 하나라도 없으면 생략 지시.
        assert "cancel_std_type" in _STRUCT_DETAIL
        assert "cancel_std_days" in _STRUCT_DETAIL
        assert "생략" in _STRUCT_DETAIL


class TestUseTimeExposedInDetailPath:
    """이제 답변 가능한 속성(이용시간)이 DETAIL 경로의 카드/LLM 컨텍스트에 노출된다.

    "마루공원 테니스장 이용시간" 류 질문은 attribute_gap 이 아니라 식별 검색(DETAIL)
    으로 흘러 use_time 이 답변/카드에 실린다(또는 triage 가 RETRIEVE 로 분류).
    """

    async def test_use_time_in_results_json_and_cards(self):
        agent = make_answer_agent("이용시간 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "use_time_start": "06:00:00",
                "use_time_end": "22:00:00",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="identification",
            message="마루공원 테니스장 이용시간 알려줘",
            hydrated_services=rows,
        )
        result = await agent.answer(state)
        # LLM 컨텍스트에 이용시간이 전달된다.
        displayed = json.loads(
            agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        )
        assert displayed[0]["use_time_start"] == "06:00:00"
        assert displayed[0]["use_time_end"] == "22:00:00"
        # 카드에도 노출된다.
        assert result["service_cards"][0]["use_time_start"] == "06:00:00"
