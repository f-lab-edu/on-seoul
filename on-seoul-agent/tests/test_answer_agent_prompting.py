"""AnswerAgent 단위 테스트 — prompting(프롬프트 조립/고지 절/정적 프롬프트).

more_notice 렌더, 자치구 판정, _build_card_system 조립, 완화 고지 게이트,
장소 프레이밍, 정적 프롬프트 캐시를 검증한다.
(test_answer_agent.py 분할: prompting)
"""

from unittest.mock import MagicMock

from agents.answer_agent import (
    AnswerAgent,
    _build_card_system,
    _more_notice,
    _has_district_in_message,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_CARD_LIST,
    _CLAUSE_RESERVATION_GUIDE,
    _CLAUSE_REFINE_HINT,
)
from schemas.state import IntentType
from tests._answer_support import _make_state, _make_agent


class TestMoreNoticeRendering:
    """'외 0건' 오출력 회귀 (코드 수준 결정적 처리).

    렌더 가능한 숫자 "0"을 LLM에 노출하지 않는다. extra_count 값에 따라
    human 입력 {more_notice} 문구를 코드에서 분기한다. LLM은 mock이므로
    프롬프트 입력 수준에서 단언한다.
    """

    def _make_rows(self, n: int) -> list[dict]:
        return [
            {"service_id": f"S{i:03d}", "service_name": f"시설{i}", "service_url": None}
            for i in range(1, n + 1)
        ]

    def test_more_notice_zero_has_no_renderable_zero(self):
        """extra_count=0 → 렌더 가능한 '0' 미표시 건수가 없고 '외' 금지 취지 문구."""
        notice = _more_notice(0)
        assert "0건" not in notice
        assert "외 N건" in notice
        assert "하지 마세요" in notice or "금지" in notice

    def test_more_notice_positive_instructs_extra_count(self):
        """extra_count>0 → '외 {n}건' 표기 지시 포함."""
        notice = _more_notice(3)
        assert "외 3건" in notice
        assert "반드시 표기" in notice

    # 5건(extra=0)/6건(extra=1) e2e more_notice 렌더는 위 단위 테스트
    # (_more_notice(0)/_more_notice(3))와 TestAnswerAgentDisplaySlice 의
    # more_notice == _more_notice(n) 단언이 이미 커버하는 동일 로직이라 축소했다.

    # ANALYTICS/FALLBACK 경로의 extra_count=0 more_notice 렌더는
    # test_exactly_five_results_more_notice_forbids_oe_n 과 동일 로직(값만 다른
    # intent 순열)이라 축소했다. 각 경로의 answer 동작은 TestAnswerAgentAnalytics /
    # TestAnswerAgentFallback 가 별도로 커버한다.


class TestHasDistrictInMessage:
    """_has_district_in_message 단위 테스트."""

    def test_official_district_name_returns_true(self):
        """공식 자치구명이 포함된 메시지는 True를 반환한다 (단일/복수 자치구 동일 로직)."""
        assert _has_district_in_message("광진구 수영장 알려줘") is True

    def test_no_district_returns_false(self):
        """자치구명이 없는 메시지는 False를 반환한다."""
        assert _has_district_in_message("수영장 알려줘") is False

    def test_informal_shortform_returns_false(self):
        """'강남' 같은 비공식 표기는 False를 반환한다 (공식명 '강남구' 미포함)."""
        assert _has_district_in_message("강남 맛집") is False

    def test_empty_string_returns_false(self):
        """빈 문자열은 False를 반환한다."""
        assert _has_district_in_message("") is False


class TestBuildCardSystem:
    """_build_card_system 골든 테스트 (Tier 2 런타임 조립)."""

    def test_reservation_only_includes_reservation_guide(self):
        """접수중 시설 있음 + 자치구 명시 → CLAUSE_RESERVATION_GUIDE 포함, CLAUSE_REFINE_HINT 미포함."""
        results = [{"service_status": "접수중"}, {"service_status": "예약마감"}]
        prompt = _build_card_system("광진구 수영장", results, None)

        assert _CLAUSE_RESERVATION_GUIDE in prompt
        assert _CLAUSE_REFINE_HINT not in prompt

    def test_no_reservation_no_district_includes_refine_hint(self):
        """접수중 없음 + 자치구 미명시(area_name None) → CLAUSE_REFINE_HINT 포함."""
        results = [{"service_status": "예약마감"}]
        prompt = _build_card_system("수영장 알려줘", results, None)

        assert _CLAUSE_REFINE_HINT in prompt
        assert _CLAUSE_RESERVATION_GUIDE not in prompt

    def test_both_conditions_includes_both_clauses(self):
        """접수중 있음 + 자치구 미명시 → 두 절 모두 포함."""
        results = [{"service_status": "접수중"}]
        prompt = _build_card_system("수영장 알려줘", results, None)

        assert _CLAUSE_RESERVATION_GUIDE in prompt
        assert _CLAUSE_REFINE_HINT in prompt

    def test_no_conditions_excludes_both_clauses(self):
        """접수중 없음 + 자치구 명시 → 두 절 모두 미포함."""
        results = [{"service_status": "예약마감"}]
        prompt = _build_card_system("강남구 수영장", results, None)

        assert _CLAUSE_RESERVATION_GUIDE not in prompt
        assert _CLAUSE_REFINE_HINT not in prompt

    def test_resolved_area_name_suppresses_refine_hint(self):
        """area_name이 해소돼 있으면(follow-up) message에 자치구 없어도 refine hint 생략.

        핵심: raw message에 "강남구" 문자열이 없어도 Router가 area_name을
        채웠으면(현재 질문 또는 history 병합) 이미 지정한 자치구를 다시 묻지 않는다.
        """
        results = [{"service_status": "예약마감"}]
        prompt = _build_card_system("그 중 무료인 것만", results, "강남구")

        assert _CLAUSE_REFINE_HINT not in prompt

    # no-area+no-district → hint 포함은 test_no_reservation_no_district_includes_refine_hint 와,
    # message내 자치구 fallback → hint 생략은 test_no_conditions_excludes_both_clauses 와
    # 동일 분기(값만 다른 순열)라 축소했다. area_name 해소 분기는
    # test_resolved_area_name_suppresses_refine_hint 가 유일 케이스로 유지한다.

    def test_always_includes_role_and_output_rules(self):
        """어떤 조건에서도 _ROLE과 _OUTPUT_RULES는 항상 포함된다."""
        prompt = _build_card_system("수영장", [], None)

        assert _ROLE in prompt
        assert _OUTPUT_RULES in prompt

    def test_always_includes_struct_card_list(self):
        """카드형 구조 블록(_STRUCT_CARD_LIST)은 항상 포함된다."""
        prompt = _build_card_system("수영장", [], None)

        assert _STRUCT_CARD_LIST[:30] in prompt  # 블록 도입부로 포함 여부 확인

    def test_empty_results_no_reservation_guide(self):
        """결과가 빈 리스트면 접수중 없음으로 처리 → CLAUSE_RESERVATION_GUIDE 미포함."""
        prompt = _build_card_system("수영장", [], None)

        assert _CLAUSE_RESERVATION_GUIDE not in prompt


class TestRelaxedNoticeGate:
    """0건 완화 재시도(retry_relaxed) 시 완화 고지 절 게이트.

    완화 사실은 결과가 1건 이상 노출될 때만 명시해야 하며,
    완화하지 않았거나(retry_relaxed=False) 완화 후에도 0건이면 노출하지 않는다
    (유료를 무료라고 오안내하거나 빈 결과에 무의미한 고지를 붙이지 않도록).
    """

    # 완화 고지 절은 동적 구성이라 고정 상수 대신 안정 마커로 검증한다.
    _RELAXED_MARKER = "완화한 결과입니다"
    _RELAXED_GUARD = "유료 시설을 무료라고 표현하지 마세요"

    def test_relaxed_with_results_includes_notice(self):
        """retry_relaxed=True + 결과 있음 → 완화 고지 절 포함."""
        results = [{"service_status": "예약마감", "payment_type": "유료"}]
        prompt = _build_card_system(
            "강남구 무료 문화행사", results, "강남구", retry_relaxed=True
        )
        assert self._RELAXED_MARKER in prompt
        assert self._RELAXED_GUARD in prompt

    def test_relaxed_with_zero_results_excludes_notice(self):
        """retry_relaxed=True 라도 결과 0건이면 완화 고지 미포함(빈 결과 오고지 방지)."""
        prompt = _build_card_system(
            "강남구 무료 문화행사", [], "강남구", retry_relaxed=True
        )
        assert self._RELAXED_MARKER not in prompt

    def test_not_relaxed_excludes_notice(self):
        """기본(retry_relaxed=False) 경로 — 결과가 있어도 완화 고지 미포함."""
        results = [{"service_status": "예약마감", "payment_type": "무료"}]
        prompt = _build_card_system("강남구 무료 문화행사", results, "강남구")
        assert self._RELAXED_MARKER not in prompt

    async def test_answer_passes_retry_relaxed_to_card_system(self):
        """answer()가 state['retry_relaxed']를 _build_card_system으로 전달해 고지 절이 실린다."""
        agent = _make_agent("완화 결과 안내입니다.")
        state = _make_state(
            hydrated_services=[
                {"service_id": "P1", "service_name": "유료시설", "payment_type": "유료"}
            ],
            retry_relaxed=True,
        )
        await agent.answer(state)
        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert self._RELAXED_MARKER in call_kwargs["system"]
        assert self._RELAXED_GUARD in call_kwargs["system"]


class TestStructCardListPlaceFraming:
    """장소 프레이밍 지시 회귀 테스트.

    이 서비스의 데이터는 '장소' 자체가 아니라 공공서비스·시설 예약 정보다.
    사용자가 '장소/곳/공간'을 직접 요구할 때 도입문에서 그 점을 짚어주도록
    지시하는 문구가 _STRUCT_CARD_LIST(및 조립 결과)에 고정되어 있는지 검증한다.
    문구가 통째로 삭제되면 RED.
    """

    # 장소 프레이밍 키워드 단순 존재 검증은 test_struct_card_list_instructs_not_a_place_framing
    # ('장소 자체'/'공공서비스' 고정)이 더 구체적으로 커버하므로 축소했다.

    def test_struct_card_list_instructs_not_a_place_framing(self):
        """장소 자체가 아니라 공공서비스·시설 예약 정보임을 짚으라는 취지 문구가 있다."""
        assert "장소 자체" in _STRUCT_CARD_LIST
        assert "공공서비스" in _STRUCT_CARD_LIST

    def test_struct_card_list_keeps_zero_result_message(self):
        """0건 안내 기존 문구는 그대로 유지된다."""
        assert "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다." in _STRUCT_CARD_LIST

    def test_build_card_system_includes_place_framing_instruction(self):
        """_build_card_system 조립 결과에도 장소 프레이밍 지시가 실린다."""
        prompt = _build_card_system("한강에서 촬영할 수 있는 장소", [], None)

        assert "장소 자체" in prompt


class TestStaticPrompts:
    """_static_prompts Tier 1 골든 테스트.

    실제 AnswerAgent.__init__을 통해 _static_prompts를 검사한다.
    MagicMock()은 LangChain 체인 조립(__or__ / with_structured_output)에 충분하다.
    """

    def _make_real_agent(self) -> AnswerAgent:
        mock_model = MagicMock()
        mock_model.__or__ = MagicMock(return_value=MagicMock())
        mock_model.with_structured_output = MagicMock(return_value=MagicMock())
        return AnswerAgent(model=mock_model)

    # MAP/ANALYTICS/FALLBACK 각 정적 프롬프트의 struct 블록 포함은 answer() chain
    # 레벨 테스트(test_map_answer_chain_receives_struct_map_in_system,
    # test_analytics_chain_receives_system_with_struct_analytics,
    # test_fallback_chain_receives_system_with_struct_fallback)가 더 end-to-end 로
    # 커버하므로 정적-레벨 포함 검증은 축소했다. ANALYTICS의 카드 블록 미포함(고유 negative)만 유지.

    def test_analytics_prompt_does_not_contain_struct_card_list(self):
        """ANALYTICS 프롬프트는 카드형 구조 블록을 포함하지 않는다."""
        agent = self._make_real_agent()
        assert (
            _STRUCT_CARD_LIST[:30]
            not in agent._static_prompts[IntentType.ANALYTICS.value]
        )
