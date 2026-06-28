"""회귀 핀 — DIRECT_ANSWER/EXPLAIN-폴백 경로가 AnswerAgent의 FALLBACK 분기를 타는지.

code-reviewer가 잡은 회귀:
    direct_answer_node가 intent를 FALLBACK으로 세팅하지 않아 AnswerAgent.answer()가
    else 분기(_build_card_system, 카드형 페르소나)로 빠졌다. 인사·잡담에 카드형
    프롬프트가 적용되는 결함. 기존 그래프 테스트가 _answer_agent를 모킹해 answer()
    내부 분기 선택을 우회했기에 1111 passed에도 검출되지 못했다.

이 파일의 핵심 원칙:
    answer()를 모킹하지 않는다. 실제 AnswerAgent.answer()를 실행하되 LLM(체인)만
    fake로 주입해 hermetic하게 만든다. 그리고 answer()가 *어떤 시스템 프롬프트를
    골랐는지*(= 어떤 intent 분기를 탔는지)를 직접 단언한다.

    - FALLBACK 분기 → _static_prompts[FALLBACK] (=_STRUCT_FALLBACK + _FALLBACK_GUARDRAILS).
      _build_card_system은 호출되지 않으므로 _STRUCT_CARD_LIST가 system에 없다.
    - else(카드) 분기 → _build_card_system이 호출되어 _STRUCT_CARD_LIST가 system에 있다.

    intent=None(회귀 상태)이면 else 분기를 타 _build_card_system이 호출되고
    _STRUCT_CARD_LIST가 노출되므로, 이 단언이 회귀를 RED로 잡는다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import agents.answer_agent as answer_agent_mod
from agents.answer_agent import (
    AnswerAgent,
    _FALLBACK_GUARDRAILS,
    _STRUCT_CARD_LIST,
    _STRUCT_FALLBACK,
)
from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from schemas.intake import IntakeAction, TurnKind
from schemas.state import ActionType, AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_intake,
    run_graph,
)


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _real_answer_agent_with_fake_llm() -> AnswerAgent:
    """실제 AnswerAgent.__init__을 거친 인스턴스 + fake LLM 체인.

    _static_prompts와 _build_card_system은 *실제* 구현을 사용한다(분기 선택을
    검증해야 하므로). 외부 LLM 호출만 차단하기 위해 _answer_chain.ainvoke /
    _title_chain.ainvoke를 AsyncMock으로 교체한다. ainvoke에 넘어온 payload는
    call_args로 관측 가능하므로 answer()가 고른 system 프롬프트를 단언할 수 있다.
    """
    mock_model = MagicMock()
    mock_model.__or__ = MagicMock(return_value=MagicMock())
    mock_model.with_structured_output = MagicMock(return_value=MagicMock())
    agent = AnswerAgent(model=mock_model)

    # LLM만 fake로 대체 — 분기 로직(_static_prompts/_build_card_system)은 그대로.
    agent._answer_chain = MagicMock()
    agent._answer_chain.ainvoke = AsyncMock(return_value="안녕하세요! 무엇을 도와드릴까요?")
    return agent


def _captured_system(agent: AnswerAgent) -> str:
    """fake _answer_chain.ainvoke에 마지막으로 전달된 system 프롬프트."""
    return agent._answer_chain.ainvoke.call_args[0][0]["system"]


# ---------------------------------------------------------------------------
# 1. 단위 — direct_answer_node 반환 state에 intent=FALLBACK이 포함되는가
# ---------------------------------------------------------------------------


class TestDirectAnswerNodeReturnsFallbackIntent:
    """direct_answer_node 반환 dict에 intent==FALLBACK이 들어있는지(단위).

    answer()를 실제로 실행하되 LLM만 fake. 반환 dict의 intent 키를 직접 단언한다.
    """

    async def test_return_dict_includes_fallback_intent(self):
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        update = await nodes.direct_answer_node(_state(message="안녕하세요", intent=None))

        assert update["plan"]["intent"] == IntentType.FALLBACK

    async def test_answer_receives_fallback_intent_in_input_state(self):
        """answer()에 전달되는 입력 state에도 intent=FALLBACK이 주입돼야 한다.

        반환 dict만이 아니라 answer() 호출 *직전* state에 intent가 주입되어야
        answer() 내부 분기가 FALLBACK을 읽는다. answer()가 고른 system이
        FALLBACK 프롬프트임을 통해 입력 state가 FALLBACK이었음을 역으로 검증.
        """
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        # intent=None(회귀 조건)으로 진입 — node가 FALLBACK을 주입해야 한다.
        await nodes.direct_answer_node(_state(message="안녕하세요", intent=None))

        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system


# ---------------------------------------------------------------------------
# 2. 분기 선택 핀 — direct_answer_node 도달 시 FALLBACK 프롬프트, 카드 프롬프트 아님
# ---------------------------------------------------------------------------


class TestDirectAnswerSelectsFallbackBranch:
    """direct_answer_node → AnswerAgent.answer()가 FALLBACK 분기를 타는지(모킹 우회 금지).

    실제 answer()를 실행하고 고른 system 프롬프트를 단언한다. 추가로
    _build_card_system이 호출되지 않았음을 spy로 못박아, intent=None 회귀가
    재발하면 (else 분기 → _build_card_system 호출 → _STRUCT_CARD_LIST 노출) RED.
    """

    async def test_fallback_prompt_selected_not_card(self):
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        await nodes.direct_answer_node(_state(message="안녕하세요", intent=None))

        system = _captured_system(agent)
        # FALLBACK 분기 = 대화형 + 가드레일 프롬프트
        assert _STRUCT_FALLBACK[:30] in system
        assert _FALLBACK_GUARDRAILS[:20] in system
        # 카드형 페르소나가 인사·잡담에 새지 않아야 한다(회귀 핀)
        assert _STRUCT_CARD_LIST[:30] not in system

    async def test_build_card_system_not_invoked(self):
        """_build_card_system 미호출을 직접 spy — answer() else 분기 진입 시 RED.

        회귀 상태(intent=None 유지)면 answer()가 else 분기로 빠져
        _build_card_system을 호출한다. 이 spy가 그 호출을 잡아낸다.
        """
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        with patch.object(
            answer_agent_mod,
            "_build_card_system",
            wraps=answer_agent_mod._build_card_system,
        ) as spy:
            await nodes.direct_answer_node(_state(message="안녕하세요", intent=None))

        spy.assert_not_called()


# ---------------------------------------------------------------------------
# 3. EXPLAIN → direct_answer 폴백도 동일하게 FALLBACK 분기를 타는가
# ---------------------------------------------------------------------------


class TestExplainFallbackSelectsFallbackBranch:
    """EXPLAIN인데 prev_reasoning이 없어 direct_answer_node로 폴백 → FALLBACK 분기.

    explain_node가 내부에서 direct_answer_node로 위임하므로, 이 경로 역시
    카드형 페르소나가 아니라 FALLBACK 대화형 프롬프트를 골라야 한다.
    """

    async def test_explain_no_prev_reasoning_uses_fallback_prompt(self):
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        update = await nodes.explain_node(_state(message="왜 그랬어?", prev_reasoning=None, intent=None))

        # 폴백 경로도 반환 dict에 FALLBACK intent를 싣는다.
        assert update["plan"]["intent"] == IntentType.FALLBACK
        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system

    async def test_explain_with_prev_reasoning_uses_explain_prompt_not_fallback(self):
        """대조군: prev_reasoning이 있으면 폴백하지 않고 EXPLAIN 재서술(S2)을 탄다.

        S2 이후 explain_node 는 단순 string 포맷팅 대신 AnswerAgent.explain() 으로
        LLM 재서술한다. FALLBACK/카드 분기가 아니라 EXPLAIN 프롬프트를 고른다.
        """
        agent = _real_answer_agent_with_fake_llm()
        nodes = GraphNodes(intake=make_intake(), answer_agent=agent)

        await nodes.explain_node(
            _state(message="왜 그랬어?", prev_reasoning="자연 체험 키워드가 있었습니다.")
        )

        agent._answer_chain.ainvoke.assert_called_once()
        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] not in system
        assert _STRUCT_CARD_LIST[:30] not in system


# ---------------------------------------------------------------------------
# 4. E2E — 그래프 전체를 실제 AnswerAgent로 관통(모킹 우회 금지)
# ---------------------------------------------------------------------------


class TestDirectAnswerBranchEndToEnd:
    """AgentGraph 전체 실행 — answer_agent를 실제 AnswerAgent(fake LLM만)로 주입.

    그래프 라우팅(triage → route_by_action → direct_answer_node) 전체를 실제
    노드로 관통시키고, 최종적으로 AnswerAgent가 FALLBACK 분기를 탔는지 확인한다.
    이는 _answer_agent 모킹으로 분기 선택을 우회하던 기존 그래프 테스트의 갭을 메운다.
    """

    async def test_direct_answer_e2e_selects_fallback_prompt(self):
        agent = _real_answer_agent_with_fake_llm()
        intake = make_intake(
            turn_kind=TurnKind.NEW, action=IntakeAction.DIRECT_ANSWER
        )
        graph = AgentGraph(intake=intake, answer_agent=agent)

        result = await run_graph(
            graph,
            _state(message="안녕하세요"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )

        assert result["triage"]["action"] == ActionType.DIRECT_ANSWER
        assert result["plan"]["intent"] == IntentType.FALLBACK
        assert "direct_answer_node" in result["node_path"]

        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system

    async def test_explain_fallback_e2e_selects_fallback_prompt(self):
        agent = _real_answer_agent_with_fake_llm()
        intake = make_intake(turn_kind=TurnKind.META)
        graph = AgentGraph(intake=intake, answer_agent=agent)

        result = await run_graph(
            graph,
            _state(message="왜 그랬어?", prev_reasoning=None),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )

        # META turn_kind → explain_node → prev_reasoning 없음 → direct_answer 폴백.
        assert result["triage"]["turn_kind"] == "META"
        assert result["plan"]["intent"] == IntentType.FALLBACK
        assert "direct_answer_node" in result["node_path"]

        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system


# ---------------------------------------------------------------------------
# 5. intent별 프롬프트 선택 매트릭스 핀 — answer()가 intent로 system을 고르는 지점 고정
# ---------------------------------------------------------------------------


class TestAnswerIntentPromptSelectionMatrix:
    """answer()가 intent에 따라 다른 system 프롬프트를 고르는 지점을 직접 단언.

    향후 누군가 분기 조건을 바꾸면(예: FALLBACK을 else로 합치면) 이 매트릭스가
    RED가 되어 동일 회귀를 잡는다. answer()를 모킹하지 않고 실제 실행한다.
    """

    async def test_fallback_intent_picks_fallback_prompt(self):
        agent = _real_answer_agent_with_fake_llm()
        await agent.answer(_state(intent=IntentType.FALLBACK, message="안녕"))
        system = _captured_system(agent)
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system

    async def test_none_intent_picks_card_prompt(self):
        """대조군: intent=None(=회귀 상태)이면 answer()는 카드형 프롬프트를 고른다.

        이것이 바로 회귀의 본질 — direct_answer_node가 None을 그대로 두면
        인사·잡담이 카드형 페르소나를 받게 된다. 이 테스트는 그 사실(intent=None
        → 카드 분기)을 명시적으로 고정해, direct_answer_node가 None을 FALLBACK으로
        바꿔야만 하는 이유를 문서화한다.
        """
        agent = _real_answer_agent_with_fake_llm()
        await agent.answer(_state(intent=None, message="안녕"))
        system = _captured_system(agent)
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_FALLBACK[:30] not in system

    async def test_sql_intent_picks_card_prompt(self):
        agent = _real_answer_agent_with_fake_llm()
        await agent.answer(_state(intent=IntentType.SQL_SEARCH, message="강남구 수영장"))
        system = _captured_system(agent)
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_FALLBACK[:30] not in system
