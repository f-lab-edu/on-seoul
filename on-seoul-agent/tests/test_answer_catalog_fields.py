"""답변 가능 속성 카탈로그 확장 (결정 A) 테스트.

hydration 이 끌어오는 보유 정형 컬럼 중 use_time_start/end, cancel_std_type/days,
tel_no 를 _normalize 가 카드/LLM 컨텍스트로 노출하는지 검증한다.
service_open_*_dt 는 신뢰불가로 계속 제외한다.
"""

import datetime
import json

from agents.answer_agent import AnswerAgent, _STRUCT_DETAIL
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

    def test_none_passthrough(self):
        out = AnswerAgent._normalize(self._row(None))
        assert out["tel_no"] is None


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
