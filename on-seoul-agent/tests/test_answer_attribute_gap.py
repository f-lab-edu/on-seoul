"""attribute_gap 전용 답변 경로 테스트.

room 63 결함 수정: triage 가 OUT_OF_SCOPE/attribute_gap 으로 정확히 분류했음에도
AnswerAgent 가 이를 DETAIL(시설 심층 서술)과 동일 신호로 받아 물어본 속성(보수공사)을
무시하고 예약 정보만 나열하던 버그를 끊는다.

핵심 검증:
- attribute_gap 신호는 DETAIL(identification)과 분리된 전용 분기를 탄다.
- 식별 성공(hydrated>=1): 데이터-성격 갭 프레이밍 + 가용 카드 안내 + 바로가기.
  카드와 모순되는 단정("정보 없음")을 system 으로 강제하지 않는다.
- 0건: 정직한 갭 안내 + 재검색/바로가기.
- 기존 DETAIL 회귀 없음(별도 파일 test_answer_detail.py 가 커버).
"""

import json

from agents.answer_agent import (
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_CARD_LIST,
    _STRUCT_DETAIL,
)
from schemas.state import ActionType, IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _gap_state(**kwargs):
    """OUT_OF_SCOPE/attribute_gap 신호를 단 상태.

    out_of_scope_node 가 세팅하는 신호(intent=VECTOR_SEARCH +
    vector_sub_intent='attribute_gap')를 재현한다.
    """
    return make_agent_state(
        action=ActionType.OUT_OF_SCOPE,
        out_of_scope_type="attribute_gap",
        intent=IntentType.VECTOR_SEARCH,
        vector_sub_intent="attribute_gap",
        **kwargs,
    )


class TestAttributeGapPromptContent:
    """전용 프롬프트가 데이터-성격 프레이밍을 강제하고 단정을 금지한다."""

    def test_prompt_frames_data_nature(self):
        # 예약 데이터 성격(예약·접수 위주, 운영 상세 미보유)을 프레이밍한다.
        assert "예약" in _STRUCT_ATTRIBUTE_GAP
        assert "바로가기" in _STRUCT_ATTRIBUTE_GAP

    def test_prompt_forbids_flat_no_info_assertion(self):
        # "X 정보는 없습니다" 식 단정 금지(카드와 모순 방지).
        assert "단정" in _STRUCT_ATTRIBUTE_GAP

    def test_prompt_forbids_fabrication(self):
        assert "지어내" in _STRUCT_ATTRIBUTE_GAP or "추측" in _STRUCT_ATTRIBUTE_GAP


class TestAttributeGapTrigger:
    """attribute_gap 신호는 DETAIL 이 아닌 전용 분기로 라우팅된다."""

    async def test_uses_attribute_gap_prompt_not_detail(self):
        agent = make_answer_agent("예약 데이터 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "area_name": "강남구",
                "service_status": "접수중",
                "payment_type": "유료",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = _gap_state(
            message="마루공원 테니스장 보수 공사에 대한 정보는?",
            hydrated_services=rows,
            user_rationale="보수 공사 정보는 제공하지 않습니다. 시설 공식 페이지를 안내해드릴게요.",
        )

        await agent.answer(state)

        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_ATTRIBUTE_GAP[:30] in system
        # DETAIL(심층 서술)·목록형 카드 프롬프트가 아님.
        assert _STRUCT_DETAIL[:30] not in system
        assert _STRUCT_CARD_LIST[:30] not in system

    async def test_cards_exposed_when_identified(self):
        """식별 성공 시 가용 카드를 그대로 노출한다(모순 없이 가진 정보 제공)."""
        agent = make_answer_agent("예약 데이터 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = _gap_state(hydrated_services=rows)
        result = await agent.answer(state)
        cards = result["service_cards"]
        assert [c["service_id"] for c in cards] == ["A1"]
        # results_json 에도 식별 시설이 전달된다.
        displayed = json.loads(
            agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        )
        assert displayed[0]["service_id"] == "A1"


class TestAttributeGapDetailMutualExclusion:
    """is_attribute_gap 과 is_detail 은 상호배타다(결정 C).

    is_detail 트리거는 vector_sub_intent == "identification" 정확 일치이므로
    "attribute_gap" 신호가 DETAIL 분기를 깨우면 안 된다(반대도 마찬가지).
    """

    async def test_attribute_gap_signal_does_not_select_detail(self):
        agent = make_answer_agent("갭 안내입니다.")
        rows = [
            {"service_id": "A1", "service_name": "마루공원 테니스장", "place_name": "마루공원"}
        ]
        state = _gap_state(hydrated_services=rows)
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_ATTRIBUTE_GAP[:30] in system
        # DETAIL 프롬프트는 선택되지 않는다(상호배타).
        assert _STRUCT_DETAIL[:30] not in system

    async def test_identification_signal_does_not_select_attribute_gap(self):
        """역방향: 정상 DETAIL(identification)은 ATTRIBUTE_GAP 분기를 타지 않는다."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="identification",
            message="마루공원 테니스장 자세히 알려줘",
            hydrated_services=rows,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_DETAIL[:30] in system
        assert _STRUCT_ATTRIBUTE_GAP[:30] not in system

    async def test_attribute_gap_suppresses_flat_more_notice(self):
        """attribute_gap 은 목록 나열형이 아니라 갭 안내형이므로 평면 '외 N건'
        꼬리표 지시를 주입하지 않는다(_more_notice(0)). DETAIL 과 동일 처리."""
        agent = make_answer_agent("갭 안내입니다.")
        # _DISPLAY_LIMIT(현재 5 이상) 초과 결과를 넣어 overflow 를 유발.
        rows = [
            {
                "service_id": f"A{i}",
                "service_name": f"마루공원 테니스장 {i}",
                "place_name": "마루공원",
            }
            for i in range(12)
        ]
        state = _gap_state(hydrated_services=rows)
        await agent.answer(state)
        notice = agent._answer_chain.ainvoke.call_args[0][0].get("more_notice", "")
        # _more_notice(0) 의 중립 지시(꼬리표 금지)가 실리고, 건수 표기 유도
        # ("N건 더 있습니다" / "'외 N건'을 반드시 표기")는 주입되지 않는다.
        assert "더 있습니다" not in notice
        assert "반드시 표기" not in notice
        assert "절대 하지 마세요" in notice


class TestAttributeGapZeroHits:
    """0건(식별 실패)이면 정직한 갭 안내 + 재검색/바로가기, 카드 없음."""

    async def test_zero_hits_no_cards(self):
        agent = make_answer_agent("예약 데이터에는 해당 상세가 없어요. 다시 찾아드릴까요?")
        state = _gap_state(hydrated_services=[])
        result = await agent.answer(state)
        assert result["service_cards"] == []
        # 0건이므로 빈 배열이 LLM 에 전달된다.
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert json.loads(call["results_json"]) == []
        # 전용 프롬프트가 실린다.
        assert _STRUCT_ATTRIBUTE_GAP[:30] in call["system"]


class TestAttributeGapRationaleSeed:
    """triage user_rationale 을 시드로 system 에 주입하되 기술 토큰은 차단."""

    async def test_rationale_seeded_into_system(self):
        agent = make_answer_agent("안내합니다.")
        rows = [{"service_id": "A1", "service_name": "마루공원 테니스장", "place_name": "마루공원"}]
        state = _gap_state(
            hydrated_services=rows,
            user_rationale="보수 공사 정보는 제공하지 않습니다.",
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert "보수 공사 정보는 제공하지 않습니다." in system


class TestAttributeGapRelaxedNotice:
    """완화 재시도(M1) 후 식별 성공 시 완화 고지 절이 함께 실린다."""

    async def test_relaxed_notice_appended_when_relaxed(self):
        agent = make_answer_agent("완화 후 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "payment_type": "유료",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]
        state = _gap_state(
            hydrated_services=rows,
            retry_relaxed=True,
            relaxed_filters=["payment_type", "area_name"],
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_ATTRIBUTE_GAP[:30] in system
        assert "완화한 결과입니다" in system
        assert "유료 시설을 무료라고 표현하지 마세요" in system
