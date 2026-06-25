"""QA 보강 회귀 — P4 메타/적합성 + attribute_gap interim(R10).

구현 에이전트 테스트(test_answer_p4_meta_relevance.py)가 다루지 않은 경계/적대 케이스만
보강한다. 중복 금지: 여기선 (1) attribute_gap 금지문구가 '부정 절' 안에서만 인용되는지
적대 점검, (2) RELEVANCE 가 적합성 근거 속성을 실제 LLM 입력에 싣는지, (3) turn_kind
분기의 정확한 동등성(REFINE/META → DESCRIBE) 경계, (4) explain 의 results_json="[]"
호출 계약 유지를 잠근다.
"""

from agents.answer_agent import (
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_DESCRIBE,
    _STRUCT_RELEVANCE,
)
from schemas.intake import TurnKind
from tests.helpers import make_agent_state, make_answer_agent


class TestAttributeGapForbiddenPhraseIsNegativeOnly:
    """적대적: 거짓 부재 단정 문구가 남았다면 반드시 '하지 마세요' 부정 절 안이어야 한다."""

    def test_flat_no_info_phrase_only_inside_prohibition(self):
        # "정보는 없습니다" 가 등장하는 모든 줄은 부정 지시(하지 마세요/금지) 줄이어야 한다.
        # 긍정 지시로 둔갑(예: "...라고 안내하세요")하면 거짓 단정을 권장하는 회귀.
        for line in _STRUCT_ATTRIBUTE_GAP.splitlines():
            if "정보는 없습니다" in line:
                assert (
                    "하지 마" in line or "마세요" in line or "금지" in line
                ), f"긍정 지시로 인용된 부재 단정 문구: {line!r}"

    def test_no_data_character_absence_assertion_recommended(self):
        # R10: "담겨있지 않" 데이터-성격 부재 단언은 전면 제거(권장도 인용도 금지).
        assert "담겨있지 않" not in _STRUCT_ATTRIBUTE_GAP
        assert "담겨 있지 않" not in _STRUCT_ATTRIBUTE_GAP
        assert "담기지 않" not in _STRUCT_ATTRIBUTE_GAP

    def test_redirect_phrasing_is_positive_instruction(self):
        # 리다이렉트(공식 페이지/바로가기 확인)는 긍정 지시로 남아 있어야 한다.
        assert "공식" in _STRUCT_ATTRIBUTE_GAP
        assert "바로가기" in _STRUCT_ATTRIBUTE_GAP
        assert "리다이렉트" in _STRUCT_ATTRIBUTE_GAP


class TestRelevanceGroundsOnAttributes:
    """RELEVANCE 가 적합성 근거가 될 속성을 실제 LLM 입력(results_json)에 싣는지."""

    async def test_hydrated_attributes_reach_llm_input(self):
        agent = make_answer_agent("산림여가/공원탐방이라 자연 속 활동입니다.")
        state = make_agent_state(
            message="왜 이 항목들이 자연속 활동이야?",
            triage={"turn_kind": TurnKind.RELEVANCE.value},
            target_service_ids=["S1"],
            hydrated_services=[
                {
                    "service_id": "S1",
                    "service_name": "북한산 둘레길 탐방",
                    "min_class_name": "산림여가",
                    "place_name": "북한산",
                    "area_name": "강북구",
                }
            ],
        )
        await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # 적합성 설명의 근거 속성(분류/장소)이 LLM 입력에 직렬화돼 들어간다.
        assert "산림여가" in call["results_json"]
        assert "북한산" in call["results_json"]


class TestTurnKindBranchExactEquality:
    """turn_kind 분기는 == RELEVANCE 정확 동등 — 다른 값은 모두 DESCRIBE."""

    def _state(self, turn_kind_value):
        return make_agent_state(
            message="왜 이 항목들이 자연속 활동이야?",
            triage={"turn_kind": turn_kind_value},
            target_service_ids=["S1"],
            hydrated_services=[
                {"service_id": "S1", "service_name": "북한산 둘레길 탐방"}
            ],
        )

    async def test_refine_uses_describe_not_relevance(self):
        # REFINE 은 RELEVANCE 가 아니다 → DESCRIBE 유지(잘못된 분기 차단).
        agent = make_answer_agent("설명입니다.")
        await agent.describe(self._state(TurnKind.REFINE.value))
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE[:30] in call["system"]
        assert _STRUCT_RELEVANCE[:30] not in call["system"]

    async def test_meta_uses_describe_not_relevance(self):
        # META 도 RELEVANCE 가 아니다 → DESCRIBE 유지.
        agent = make_answer_agent("설명입니다.")
        await agent.describe(self._state(TurnKind.META.value))
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE[:30] in call["system"]
        assert _STRUCT_RELEVANCE[:30] not in call["system"]

    async def test_unknown_string_turn_kind_uses_describe(self):
        # 미래/오염 문자열 turn_kind 도 RELEVANCE 외엔 DESCRIBE(안전 폴백).
        agent = make_answer_agent("설명입니다.")
        await agent.describe(self._state("SOMETHING_NEW"))
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE[:30] in call["system"]


class TestExplainCallContract:
    """explain 은 빈 결과(results_json="[]")로 호출되며 카드를 노출하지 않는다."""

    async def test_explain_calls_with_empty_results_and_no_cards(self):
        agent = make_answer_agent("그렇게 판단한 이유는 ...")
        state = make_agent_state(
            message="왜 그렇게 판단했어?",
            prev_reasoning="자연 친화 키워드와 일치하는 시설을 골랐습니다.",
        )
        result = await agent.explain(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # no-results 변질 방지의 전제: 빈 배열로 호출(설계 624-625)되며 가드가 system 에 실림.
        assert call["results_json"] == "[]"
        assert result["service_cards"] == []
