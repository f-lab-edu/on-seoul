"""TriageAgent 단위 테스트 — action 결정 전담.

실제 LLM 호출 없이 Mock LLM으로 action 분류 동작을 검증한다.
검색 계획(intent/refined_query/post-filter/secondary_intent)은 RouterAgent 책임이므로
TriageOutput 에 없다 — 해당 검증은 test_router_agent.py 가 담당한다.
"""

from unittest.mock import AsyncMock, MagicMock


from agents.triage_agent import TriageAgent, TriageOutput
from schemas.state import ActionType


def _make_triage(
    action: ActionType,
    out_of_scope_type: str | None = None,
    user_rationale: str | None = None,
) -> TriageAgent:
    """지정된 output을 반환하는 Mock LLM이 주입된 TriageAgent."""
    agent = TriageAgent.__new__(TriageAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(
        return_value=TriageOutput(
            action=action,
            out_of_scope_type=out_of_scope_type,  # type: ignore[arg-type]
            user_rationale=user_rationale,
        )
    )
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    agent._build_context_block = lambda history: ""
    return agent


class TestTriageAgentActionClassification:
    async def test_retrieve_action(self):
        """RETRIEVE action이 반환된다 (검색 방식 결정은 Router 책임)."""
        agent = _make_triage(ActionType.RETRIEVE)
        result = await agent.classify("마포구 접수중인 문화행사")
        assert result.action == ActionType.RETRIEVE

    async def test_direct_answer_action(self):
        """DIRECT_ANSWER action이 반환된다."""
        agent = _make_triage(ActionType.DIRECT_ANSWER)
        result = await agent.classify("안녕하세요")
        assert result.action == ActionType.DIRECT_ANSWER

    async def test_ambiguous_action(self):
        """AMBIGUOUS action이 반환된다."""
        agent = _make_triage(ActionType.AMBIGUOUS)
        result = await agent.classify("좋은 곳 알려줘")
        assert result.action == ActionType.AMBIGUOUS

    async def test_out_of_scope_domain_outside(self):
        """OUT_OF_SCOPE/domain_outside action이 반환된다."""
        agent = _make_triage(
            ActionType.OUT_OF_SCOPE, out_of_scope_type="domain_outside"
        )
        result = await agent.classify("오늘 서울 날씨")
        assert result.action == ActionType.OUT_OF_SCOPE
        assert result.out_of_scope_type == "domain_outside"

    async def test_out_of_scope_attribute_gap(self):
        """OUT_OF_SCOPE/attribute_gap action이 반환된다."""
        agent = _make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
        )
        result = await agent.classify("마루공원 테니스장 보수 공사 일정")
        assert result.action == ActionType.OUT_OF_SCOPE
        assert result.out_of_scope_type == "attribute_gap"

    async def test_explain_action(self):
        """EXPLAIN action이 반환된다."""
        agent = _make_triage(ActionType.EXPLAIN)
        result = await agent.classify("왜 그렇게 판단했어?")
        assert result.action == ActionType.EXPLAIN

    async def test_user_rationale_included(self):
        """user_rationale 필드가 반환된다."""
        agent = _make_triage(
            ActionType.RETRIEVE,
            user_rationale="마포구 문화행사를 검색합니다.",
        )
        result = await agent.classify("마포구 문화행사")
        assert result.user_rationale == "마포구 문화행사를 검색합니다."


class TestTriageOutputShape:
    def test_output_has_no_retrieval_fields(self):
        """TriageOutput에는 검색 계획 필드가 없다 (Router로 이관됨)."""
        fields = set(TriageOutput.model_fields.keys())
        assert fields == {
            "reasoning",
            "action",
            "out_of_scope_type",
            "user_rationale",
        }

    def test_minimal_construction(self):
        """action만으로 구성 가능하다."""
        out = TriageOutput(action=ActionType.RETRIEVE)
        assert out.action == ActionType.RETRIEVE
        assert out.out_of_scope_type is None
        assert out.user_rationale is None


# 답변 가능 속성 카탈로그 grounding (결정 A의 예방축)
class TestTriageCatalogGrounding:
    """이용시간/취소기준/문의처가 답변 가능 속성으로 명시되어 attribute_gap
    오분류를 예방하는지 검증한다(결정 A). 기존 attribute_gap 예시(보수공사 등)는 유지."""

    def test_answerable_catalog_lists_newly_answerable_attributes(self):
        from llm.prompts.triage import TRIAGE_SYSTEM

        assert "이용시간" in TRIAGE_SYSTEM
        assert "취소기준" in TRIAGE_SYSTEM
        assert "문의처" in TRIAGE_SYSTEM

    def test_attribute_gap_examples_preserved(self):
        from llm.prompts.triage import TRIAGE_SYSTEM

        # 여전히 답변 불가한 속성 예시는 유지.
        assert "보수공사" in TRIAGE_SYSTEM or "보수 공사" in TRIAGE_SYSTEM

    def test_maru_few_shot_preserved(self):
        from llm.prompts.triage import TRIAGE_FEW_SHOT_EXAMPLES

        messages = [ex["message"] for ex in TRIAGE_FEW_SHOT_EXAMPLES]
        assert any("마루공원" in m for m in messages)


# FALLBACK 액션이 ActionType에 존재하지 않음을 명시적으로 확인 (test_triage_gaps.py 에서 이관)
class TestFallbackActionRemoved:
    def test_no_fallback_in_action_type(self):
        """ActionType에 FALLBACK 멤버가 없어야 한다 (DIRECT_ANSWER로 대체됨)."""
        action_values = {a.value for a in ActionType}
        assert "FALLBACK" not in action_values

    def test_five_action_types_exist(self):
        """ActionType은 정확히 5개 멤버를 가진다."""
        assert len(ActionType) == 5
        expected = {"RETRIEVE", "DIRECT_ANSWER", "AMBIGUOUS", "OUT_OF_SCOPE", "EXPLAIN"}
        actual = {a.value for a in ActionType}
        assert actual == expected
