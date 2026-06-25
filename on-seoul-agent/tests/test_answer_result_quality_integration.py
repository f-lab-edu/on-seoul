"""사례 161 완결 통합 테스트 — answer() 가 result_quality/reservation_guide_shown
state 슬롯을 _build_card_system 으로 흘려 SKEW_OFFER 톤을 적용하는지 검증한다.

fake LLM(chain mock)으로 system 프롬프트 내용만 assert 한다(실제 LLM 미호출).
"""

from agents.answer_agent import (
    _CLAUSE_REFINE_HINT,
    _CLAUSE_RESERVATION_GUIDE,
    _CLAUSE_SKEW_OFFER,
)
from schemas.state import IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _hydrated(areas, status="접수중"):
    return [
        {
            "service_id": f"P{i}",
            "service_name": f"강남시설{i}",
            "area_name": a,
            "service_status": status,
        }
        for i, a in enumerate(areas)
    ]


class TestCase161AnswerWiring:
    async def test_skew_offer_replaces_refine_hint_in_answer(self):
        """사례 161: 지역 미지정 + 결과 전부 강남 → SKEW_OFFER 톤, '어느 구냐' 미질문."""
        agent = make_answer_agent("강남구 체육시설 안내입니다.")
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            message="지금 접수중 체육시설",
            hydrated_services=_hydrated(["강남구"] * 5),
            result_quality={
                "skew_field": "area_name",
                "skew_value": "강남구",
                "thin": False,
            },
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") in system
        assert _CLAUSE_REFINE_HINT not in system

    async def test_reservation_guide_shown_suppresses_in_answer(self):
        """멀티턴: 직전에 안내했으면(reservation_guide_shown=True) 통합회원 안내 생략."""
        agent = make_answer_agent("안내입니다.")
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            message="강남구 수영장",
            area_name="강남구",
            hydrated_services=_hydrated(["강남구"] * 3, status="접수중"),
            reservation_guide_shown=True,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _CLAUSE_RESERVATION_GUIDE not in system

    async def test_no_quality_flag_keeps_current_behavior(self):
        """result_quality 미설정 → 현행 조립(지역 미지정 → REFINE_HINT)."""
        agent = make_answer_agent("안내입니다.")
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            message="체육시설",
            hydrated_services=_hydrated(["강남구", "마포구"], status="예약마감"),
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _CLAUSE_REFINE_HINT in system
        assert _CLAUSE_SKEW_OFFER not in system
