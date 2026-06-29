"""메타/적합성 후속 graceful 처리 + attribute_gap 거짓 부재 단정 제거(interim).

사례 156(적합성 "왜 이 항목들?"이 현재형 no-results로 변질) + 162-163(attribute_gap
이 detail_content 로 답할 수 있는 운영성 질문을 "없다"고 거짓 단정) 대응.

검증(프롬프트 가드: fake LLM 응답 고정 + 호출 인자/system 텍스트 assert):
- describe: turn_kind=RELEVANCE → 적합성 설명 프롬프트(_STRUCT_RELEVANCE) + 원 성격
  키워드(사용자 발화) 주입. DRILL/기본 → 현행 _STRUCT_DESCRIBE(회귀 가드).
- explain: _STRUCT_EXPLAIN 에 "결과 없음/못 찾았다로 답하지 말라" 가드 포함.
- attribute_gap: _STRUCT_ATTRIBUTE_GAP 에 부재 단정 문구 없음 + 리다이렉트 지시.
"""

from agents.answer_agent import (
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_DESCRIBE,
    _STRUCT_EXPLAIN,
    _STRUCT_RELEVANCE,
)
from schemas.intake import TurnKind
from tests.helpers import make_agent_state, make_answer_agent


class TestAttributeGapNoAbsenceAssertion:
    """interim: attribute_gap 프롬프트가 부재를 단정하지 않는다."""

    def test_no_contains_absence_assertion(self):
        # "담겨있지 않" 류 부재 단정을 권장/노출하지 않는다(부재 단언 거짓 방지).
        assert "담겨있지 않" not in _STRUCT_ATTRIBUTE_GAP

    def test_redirect_to_official_page_present(self):
        # 단정 회피 — 공식 페이지/바로가기 리다이렉트 지시는 유지.
        assert "바로가기" in _STRUCT_ATTRIBUTE_GAP or "공식" in _STRUCT_ATTRIBUTE_GAP

    def test_forbids_flat_no_info_assertion_still(self):
        # "(물어본 속성) 정보는 없습니다" 류 단정 금지 규칙은 유지.
        assert "단정" in _STRUCT_ATTRIBUTE_GAP

    def test_no_fabrication_rule_retained(self):
        assert "지어내" in _STRUCT_ATTRIBUTE_GAP or "추측" in _STRUCT_ATTRIBUTE_GAP


class TestExplainNoResultsGuard:
    """explain(META) 이 현재형 no-results 로 변질되지 않도록 가드 포함."""

    def test_explain_prompt_has_no_results_guard(self):
        # 새 검색이 아니라 직전 판단의 근거 설명이며 "결과 없음/못 찾았다"로 답하지 말 것.
        assert "결과" in _STRUCT_EXPLAIN
        assert "못 찾" in _STRUCT_EXPLAIN or "찾지 못" in _STRUCT_EXPLAIN

    async def test_explain_seeds_guard_into_system(self):
        agent = make_answer_agent("그렇게 안내드린 이유를 설명드릴게요.")
        state = make_agent_state(
            message="왜 그렇게 판단했어?",
            prev_reasoning="자연 친화 키워드와 일치하는 시설을 골랐습니다.",
        )
        await agent.explain(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # EXPLAIN 정적 프롬프트가 실리고, no-results 가드 문구가 포함된다.
        assert _STRUCT_EXPLAIN[:30] in call["system"]
        assert "못 찾" in call["system"] or "찾지 못" in call["system"]
        # 실제 사용자 질문은 human message 자리에 전달된다.
        assert call["message"] == "왜 그렇게 판단했어?"
        # 직전 근거는 보조 맥락으로 system 에 경계 마커로 감싸 전달(회귀 가드).
        assert "---REASONING_START---" in call["system"]


class TestDescribeRelevanceVariant:
    """describe 가 turn_kind 로 RELEVANCE/DRILL 을 분기한다."""

    def _state(self, turn_kind, **kw):
        return make_agent_state(
            message="왜 이 항목들이 자연속 활동이야?",
            triage={"turn_kind": turn_kind},
            target_service_ids=["S1", "S2"],
            hydrated_services=[
                {
                    "service_id": "S1",
                    "service_name": "북한산 둘레길 탐방",
                    "min_class_name": "산림여가",
                    "place_name": "북한산",
                    "area_name": "강북구",
                    "service_url": "https://yeyak.seoul.go.kr/s1",
                },
                {
                    "service_id": "S2",
                    "service_name": "서울숲 공원탐방",
                    "min_class_name": "공원탐방",
                    "place_name": "서울숲",
                    "area_name": "성동구",
                    "service_url": "https://yeyak.seoul.go.kr/s2",
                },
            ],
            **kw,
        )

    async def test_relevance_uses_relevance_prompt(self):
        agent = make_answer_agent("산림여가/공원탐방 분류라 자연 속 활동입니다.")
        state = self._state(TurnKind.RELEVANCE.value)
        await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # 적합성 설명 프롬프트가 선택된다 — "어떤 곳인지" 설명형(_STRUCT_DESCRIBE)이 아님.
        assert _STRUCT_RELEVANCE[:30] in call["system"]
        assert _STRUCT_DESCRIBE[:30] not in call["system"]

    async def test_relevance_prompt_explains_fitness(self):
        # 신규 프롬프트는 "왜 이게 맞는지"를 결과 속성으로 묶어 설명하라는 지시를 담는다.
        assert "왜" in _STRUCT_RELEVANCE
        assert "관련" in _STRUCT_RELEVANCE or "맞는" in _STRUCT_RELEVANCE

    async def test_relevance_injects_user_query_keyword(self):
        # 원 성격 키워드(사용자 발화)가 LLM 입력에 실린다(message 경유).
        agent = make_answer_agent("자연 속 활동에 맞습니다.")
        state = self._state(TurnKind.RELEVANCE.value)
        await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert "자연속 활동" in call["message"]

    async def test_drill_keeps_describe_prompt(self):
        # DRILL(개별 상세)은 현행 describe 유지(회귀 가드).
        agent = make_answer_agent("북한산 둘레길 탐방은 강북구 산림여가 시설입니다.")
        state = self._state(TurnKind.DRILL.value)
        await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE[:30] in call["system"]
        assert _STRUCT_RELEVANCE[:30] not in call["system"]

    async def test_no_turn_kind_keeps_describe_prompt(self):
        # turn_kind 미설정(구 경로) → 현행 describe(완전 하위호환).
        agent = make_answer_agent("설명입니다.")
        state = make_agent_state(
            message="이 곳 어떤 곳이야?",
            target_service_ids=["S1"],
            hydrated_services=[
                {"service_id": "S1", "service_name": "마루공원 테니스장"}
            ],
        )
        await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE[:30] in call["system"]

    async def test_relevance_empty_hydrate_uses_describe_empty(self):
        # RELEVANCE 라도 재-hydrate 0건이면 DESCRIBE_EMPTY 정직 안내(환각 금지).
        from agents.answer_agent import _STRUCT_DESCRIBE_EMPTY

        agent = make_answer_agent("지금은 확인이 어렵습니다. 다시 찾아드릴까요?")
        state = make_agent_state(
            message="왜 이 항목들이 자연속 활동이야?",
            triage={"turn_kind": TurnKind.RELEVANCE.value},
            target_service_ids=["S1", "S2"],
            hydrated_services=[],
        )
        result = await agent.describe(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_DESCRIBE_EMPTY[:30] in call["system"]
        assert result["service_cards"] == []


class TestRelevanceCardsExposed:
    """RELEVANCE 도 재-hydrate 원본을 카드로 노출(describe 와 동일 계약)."""

    async def test_cards_exposed_in_relevance(self):
        agent = make_answer_agent("두 시설 모두 자연 속 활동입니다.")
        state = make_agent_state(
            message="왜 이 항목들이 자연속 활동이야?",
            triage={"turn_kind": TurnKind.RELEVANCE.value},
            target_service_ids=["S1", "S2"],
            hydrated_services=[
                {"service_id": "S1", "service_name": "북한산 둘레길 탐방"},
                {"service_id": "S2", "service_name": "서울숲 공원탐방"},
            ],
        )
        result = await agent.describe(state)
        ids = [c["service_id"] for c in result["service_cards"]]
        assert ids == ["S1", "S2"]
