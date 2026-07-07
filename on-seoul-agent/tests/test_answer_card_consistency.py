"""answer 카드형 경로가 상태의 curated 슬롯을 렌더링만 한다.

answer 는 자체 all_results[:5] 슬라이스·extra_count 계산을 하지 않고 pre_answer_gate
가 적재한 curated_display/curated_extra_count 를 읽는다. service_cards=display,
"외 N건"=curated 잔여, 프롬프트엔 display 만 전달(카드=prose=display 정합).
"""

import json

from agents.answer_agent import _STRUCT_CARD_LIST, _more_notice
from schemas.state import IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _card(sid, *, area="광진구", status="접수중"):
    return {
        "service_id": sid,
        "service_name": sid,
        "area_name": area,
        "service_status": status,
    }


class TestAnswerReadsCuratedSlots:
    async def test_service_cards_equal_curated_display(self):
        agent = make_answer_agent("답변")
        display = [_card("A"), _card("B"), _card("C")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[_card(f"X{i}") for i in range(9)],
            curated_display=display,
            curated_extra_count=2,
            curated_alt_count=0,
        )
        out = await agent.answer(state)
        # 카드 = curated_display (자체 슬라이스 아님).
        assert [c["service_id"] for c in out["service_cards"]] == ["A", "B", "C"]

    async def test_results_json_is_curated_display(self):
        agent = make_answer_agent("답변")
        display = [_card("A"), _card("B")]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            hydrated_services=[_card(f"X{i}") for i in range(9)],
            curated_display=display,
            curated_extra_count=3,
            curated_alt_count=0,
        )
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        sent = json.loads(call["results_json"])
        # LLM 입력 = display 그대로(2건). 카드=prose=display 정합.
        assert [r["service_id"] for r in sent] == ["A", "B"]

    async def test_more_notice_uses_curated_extra_count(self):
        agent = make_answer_agent("답변")
        display = [_card(c) for c in "ABCDE"]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[_card(f"X{i}") for i in range(20)],
            curated_display=display,
            curated_extra_count=2,
            curated_alt_count=0,
        )
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # "외 N건" = curated 잔여(2), raw(20-5=15) 아님.
        assert call["more_notice"] == _more_notice(2)

    async def test_alt_label_clause_present_when_alt_count(self):
        agent = make_answer_agent("답변")
        display = [_card("EXACT"), _card("ALT", area="서초구")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            area_name="광진구",
            hydrated_services=display,
            curated_display=display,
            curated_extra_count=0,
            curated_alt_count=1,
        )
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # 딱맞음/대안 구분 라벨 지시가 system 에 실린다.
        assert "비슷한" in call["system"] or "대안" in call["system"]

    async def test_no_alt_label_when_all_exact(self):
        agent = make_answer_agent("답변")
        display = [_card("A"), _card("B")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            area_name="광진구",
            hydrated_services=display,
            curated_display=display,
            curated_extra_count=0,
            curated_alt_count=0,
        )
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert "비슷한 시설" not in call["system"]


class TestStructCardListPrompt:
    def test_one_to_one_listing_directive(self):
        # 1:1 빠짐없이 나열 + 임의 제외 금지 지시.
        assert "빠짐없이" in _STRUCT_CARD_LIST
        assert "제외" in _STRUCT_CARD_LIST
