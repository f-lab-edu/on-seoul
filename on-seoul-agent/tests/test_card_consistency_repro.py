"""통합 재현 — 207/209 카드↔prose↔"외 N건" 정합 + 부적합 강등/대안 라벨.

pre_answer_gate_node(큐레이션) → answer(렌더링) 2노드를 실제 데이터 흐름대로 엮어,
카드=display, "외 N"=curated 잔여, 부적합 항목 강등/대안 라벨을 가드한다.
fake LLM(answer chain mock)으로 LLM 입력만 검증(실제 LLM 미호출).
"""

import json
from unittest.mock import MagicMock

from agents.answer_agent import _more_notice
from agents.nodes.retrieval import RetrievalNodes
from schemas.state import ActionType, IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _nodes() -> RetrievalNodes:
    return RetrievalNodes(
        sql=MagicMock(),
        vector=MagicMock(),
        analytics=MagicMock(),
        hydration=MagicMock(),
        ondata=MagicMock(),
    )


def _row(sid, *, area, klass=None, pay=None, status="접수중"):
    return {
        "service_id": sid,
        "service_name": sid,
        "place_name": sid,
        "area_name": area,
        "max_class_name": klass,
        "payment_type": pay,
        "service_status": status,
    }


async def _run(state):
    """pre_answer_gate → answer 를 실제 흐름대로 실행하고 (gate_out, answer_chain_call)."""
    gate_out = await _nodes().pre_answer_gate_node(state)
    merged = {
        **state,
        "curated_display": gate_out["curated_display"],
        "curated_extra_count": gate_out["curated_extra_count"],
        "curated_alt_count": gate_out["curated_alt_count"],
        "result_quality": gate_out["result_quality"],
    }
    agent = make_answer_agent("답변")
    out = await agent.answer(merged)
    call = agent._answer_chain.ainvoke.call_args[0][0]
    return gate_out, out, call


class TestCase207:
    """광진구 데이트 프로그램(VECTOR) — 마감 항목이 카드 하단으로 강등, prose=카드 개수.

    이전: 카드 3건인데 prose 2건(LLM 이 접수종료를 숨김). 이제 큐레이션이 강등하고
    answer 는 1:1 나열 → prose 개수 = 카드 개수.
    """

    async def test_prose_card_count_match_and_closed_demoted(self):
        rows = [
            _row("OPEN1", area="광진구", status="접수중"),
            _row("CLOSED", area="광진구", status="접수종료"),
            _row("OPEN2", area="광진구", status="접수중"),
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.RETRIEVE,
            message="광진구 데이트 프로그램",
            area_name="광진구",
            hydrated_services=rows,
        )
        gate_out, out, call = await _run(state)
        cards = out["service_cards"]
        sent = json.loads(call["results_json"])
        # 카드 = LLM 입력(prose 원천) = 3건 정합.
        assert len(cards) == len(sent) == 3
        # 접수종료는 하단으로 강등(상단은 접수중).
        assert cards[0]["service_status"] == "접수중"
        assert cards[-1]["service_id"] == "CLOSED"
        # 모두 표시 → "외 N건" 금지 문구.
        assert call["more_notice"] == _more_notice(0)


class TestCase209:
    """광진구 무료 체육시설(SQL 0건→완화 VECTOR) — 타지역/타카테고리 강등 + 대안 라벨.

    이전: 카드 5건(서초구 양재천/청년센터 등 부적합 섞임), prose 3건 + "외 4건"(어긋남).
    이제 적합도 정렬로 광진구 체육시설이 상단, 서초구는 강등/상한 밖, 카드=prose=display,
    "외 N"=curated 잔여, 완화 라벨.
    """

    async def test_cards_equal_prose_and_relevant_top(self):
        # 완화 후 filters 는 비고(드롭됨), relaxed_values 로 의도 복원.
        rows = [
            _row("SEOCHO", area="서초구", klass="체육시설", pay="유료"),
            _row("GWANGJIN_GYM", area="광진구", klass="체육시설", pay="유료"),
            _row("GWANGJIN_CULT", area="광진구", klass="문화행사", pay="무료"),
            _row("OTHER1", area="강남구", klass="문화행사", pay="유료"),
            _row("OTHER2", area="강북구", klass="교육", pay="유료"),
            _row("OTHER3", area="송파구", klass="교육", pay="유료"),
            _row("OTHER4", area="노원구", klass="교육", pay="유료"),
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.RETRIEVE,
            message="광진구 무료 체육시설",
            hydrated_services=rows,
            retry_relaxed=True,
            relaxed_filters=["area_name", "payment_type", "max_class_name"],
            relaxed_values={
                "area_name": "광진구",
                "payment_type": "무료",
                "max_class_name": "체육시설",
            },
        )
        gate_out, out, call = await _run(state)
        cards = out["service_cards"]
        sent = json.loads(call["results_json"])
        # 카드 = prose 원천 = 5건(display 상한), 정합.
        assert len(cards) == len(sent) == 5
        # 광진구 체육시설(area+category 매칭)이 서초구보다 상단.
        ids = [c["service_id"] for c in cards]
        assert ids.index("GWANGJIN_GYM") < ids.index("SEOCHO")
        # "외 N" = curated 잔여(7-5=2).
        assert gate_out["curated_extra_count"] == 2
        assert call["more_notice"] == _more_notice(2)
        # 완화 안내가 system 에 실린다(완화 사실 고지).
        assert "완화" in call["system"]
