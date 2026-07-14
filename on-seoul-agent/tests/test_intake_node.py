"""intake_node 단위/에이전트 테스트 (fake LLM) — turn_kind 분류·참조·폴백·decision.

검증:
- 5종 turn_kind 분류 + ref_indices 바인딩
- (A) 분류 모호 폴백: 미지/누락 → NEW + RETRIEVE + breadcrumb
- (B) 노드 예외 폴백: DIRECT_ANSWER + error + intake_error
- 참조 바인딩 실패(빈 prev_entities) → NEW 폴백(환각 ID 0)
- decision 단일 발행 + grounding 문구 system 포함
- 하위호환: prev_entities 미전송 → 순수 분류
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from agents.intake_agent import IntakeAgent
from agents.nodes.intake import IntakeNodes
from llm.prompts.intake import INTAKE_FEW_SHOT_EXAMPLES, INTAKE_GROUNDING
from schemas.intake import IntakeAction, IntakeOutput, TurnKind
from schemas.state import ActionType
from tests.helpers import make_agent_state, make_intake

_PREV = [
    {"service_id": "S1", "label": "남산 숲 체험"},
    {"service_id": "S2", "label": "한강 자연 관찰"},
    {"service_id": "S3", "label": "도봉산 탐방"},
]


def _nodes(intake: IntakeAgent) -> IntakeNodes:
    return IntakeNodes(intake=intake)


class TestTurnKindClassification:
    async def test_new_retrieve(self):
        node = _nodes(make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE))
        state = make_agent_state(message="마포 수영장 알려줘")
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["target_service_ids"] is None

    async def test_new_direct_answer_maps_action(self):
        node = _nodes(
            make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.DIRECT_ANSWER)
        )
        update = await node.intake_node(make_agent_state(message="안녕하세요"))
        assert update["triage"]["action"] == ActionType.DIRECT_ANSWER

    async def test_new_out_of_scope_carries_oos_type(self):
        node = _nodes(
            make_intake(
                turn_kind=TurnKind.NEW,
                action=IntakeAction.OUT_OF_SCOPE,
                oos_type="operational_detail",
            )
        )
        update = await node.intake_node(make_agent_state(message="폭염철 안내"))
        assert update["triage"]["action"] == ActionType.OUT_OF_SCOPE
        assert update["triage"]["out_of_scope_type"] == "operational_detail"

    async def test_drill_binds_single_index(self):
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[2]))
        state = make_agent_state(message="두 번째 자세히", prev_entities=_PREV)
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "DRILL"
        assert update["target_service_ids"] == ["S2"]

    async def test_relevance_binds_set(self):
        node = _nodes(make_intake(turn_kind=TurnKind.RELEVANCE, ref_indices=[1, 2, 3]))
        state = make_agent_state(message="왜 이 항목들이?", prev_entities=_PREV)
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "RELEVANCE"
        assert update["target_service_ids"] == ["S1", "S2", "S3"]

    async def test_meta_no_binding(self):
        node = _nodes(make_intake(turn_kind=TurnKind.META, ref_indices=[]))
        state = make_agent_state(
            message="왜 그렇게 판단했어?",
            prev_entities=_PREV,
            prev_reasoning="자연 키워드 매칭",
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "META"
        assert update["target_service_ids"] is None


class TestFallbackA:
    async def test_ref_binding_failure_degrades_to_new(self):
        # DRILL 인데 prev_entities 비어 바인딩 실패 → NEW + RETRIEVE + breadcrumb.
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[2]))
        state = make_agent_state(message="두 번째", prev_entities=[])
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["target_service_ids"] is None
        assert "intake_route_fallback" in update["node_path"]

    async def test_out_of_range_index_degrades(self):
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[9]))
        state = make_agent_state(message="아홉 번째", prev_entities=_PREV)
        update = await node.intake_node(state)
        # 범위 밖 → 바인딩 0 → NEW 폴백(환각 ID 0).
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["target_service_ids"] is None
        assert "intake_route_fallback" in update["node_path"]


class TestFallbackB:
    async def test_node_exception_direct_answer(self):
        node = _nodes(make_intake(raise_exc=RuntimeError("boom")))
        update = await node.intake_node(make_agent_state(message="x"))
        assert update["triage"]["action"] == ActionType.DIRECT_ANSWER
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["error"] == "boom"
        assert update["output"]["answer"]
        assert "intake_error" in update["node_path"]

    async def test_node_exception_emits_answering_guard(self):
        # SHOULD-FIX 2 — 예외 폴백도 answering 가드를 흘려 정상 비-RETRIEVE 경로
        # (_emit_intake)와 SSE progress 시퀀스를 대칭으로 맞춘다(관측 일관성).
        node = _nodes(make_intake(raise_exc=RuntimeError("boom")))
        update = await node.intake_node(make_agent_state(message="x"))
        assert update["emit"]["answering_emitted"] is True


class TestGroundingPrompt:
    async def test_system_prompt_contains_grounding(self):
        # 실제 IntakeAgent(가짜 LLM)로 classify 를 돌려 system 텍스트를 캡처.
        agent = IntakeAgent.__new__(IntakeAgent)
        structured = MagicMock()

        captured = {}

        async def _capture(messages):
            captured["system"] = messages[0].content
            from schemas.intake import IntakeOutput

            return IntakeOutput(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)

        structured.ainvoke = AsyncMock(side_effect=_capture)
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        agent._llm = llm

        await agent.classify("테스트", prev_entities=_PREV)
        # grounding 카탈로그 일부 문구 + 열거된 prev_entities 가 system 에 포함.
        assert "operational_detail" in captured["system"]
        assert INTAKE_GROUNDING.splitlines()[0] in captured["system"]
        assert "1. 남산 숲 체험" in captured["system"]


class TestWeatherConditionedFollowUp:
    """T3 — 기상 조건 하 운영여부 후속은 맥락 후속(REFINE/DRILL), domain_outside 아님."""

    def test_grounding_has_weather_operation_boundary(self):
        # grounding 에 "폭염에도 운영해?" 맥락 후속 규칙 + 순수 날씨 경계가 있는지.
        assert "폭염에도 운영해?" in INTAKE_GROUNDING
        assert "운영여부" in INTAKE_GROUNDING
        assert "오늘 날씨 어때?" in INTAKE_GROUNDING

    def test_fewshot_weather_followup_not_domain_outside(self):
        ex = next(
            (e for e in INTAKE_FEW_SHOT_EXAMPLES if "폭염에도 운영해?" in e["message"]),
            None,
        )
        assert ex is not None
        out = json.loads(ex["output"])
        # 분류 결과가 맥락 후속(REFINE/DRILL)이고 OUT_OF_SCOPE/domain_outside 아님.
        assert out["turn_kind"] in ("REFINE", "DRILL")
        assert out["action"] != "OUT_OF_SCOPE"
        assert out["oos_type"] is None

    def test_fewshot_pure_weather_stays_domain_outside(self):
        # 경계 회귀: 순수 날씨 질의는 여전히 domain_outside few-shot.
        ex = next(
            (e for e in INTAKE_FEW_SHOT_EXAMPLES if "오늘 서울 날씨" in e["message"]),
            None,
        )
        assert ex is not None
        assert "domain_outside" in ex["output"]

    async def test_weather_followup_binds_drill_not_oos(self):
        # 직전 단건 맥락에서 "폭염에도 운영해?" → DRILL 바인딩, action!=OUT_OF_SCOPE.
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[1]))
        state = make_agent_state(
            message="폭염에도 운영해?",
            prev_entities=[{"service_id": "S1", "label": "마루공원 테니스장"}],
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "DRILL"
        assert update["triage"]["action"] != ActionType.OUT_OF_SCOPE
        assert update["target_service_ids"] == ["S1"]

    async def test_pure_weather_stays_domain_outside(self):
        # 경계 회귀: 순수 날씨 질의는 NEW + OUT_OF_SCOPE/domain_outside 유지.
        node = _nodes(
            make_intake(
                turn_kind=TurnKind.NEW,
                action=IntakeAction.OUT_OF_SCOPE,
                oos_type="domain_outside",
            )
        )
        update = await node.intake_node(make_agent_state(message="오늘 날씨 어때?"))
        assert update["triage"]["action"] == ActionType.OUT_OF_SCOPE
        assert update["triage"]["out_of_scope_type"] == "domain_outside"


class TestBackwardCompat:
    async def test_no_prev_entities_pure_classification(self):
        node = _nodes(make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE))
        state = make_agent_state(message="수영장")  # prev_entities=None
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["target_service_ids"] is None


# ---------------------------------------------------------------------------
# QA 추가 회귀 — 갭 보강 (action 폴백, 라벨 노출,
# 적대적 인덱스 타입, route_intake 분기 도달)
# ---------------------------------------------------------------------------


def _intake_returning(out: IntakeOutput) -> IntakeAgent:
    """임의 IntakeOutput(스키마 밖 변조 포함)을 반환하는 IntakeAgent fake."""
    agent = make_intake()
    agent._llm.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value=out
    )
    return agent


class TestActionFallbackBreadcrumb:
    """폴백 두 번째 층: NEW 인데 action 이 매핑 불가 → RETRIEVE 강등 + breadcrumb.

    turn_kind 폴백과 별개로 action 폴백 breadcrumb 가
    남는지 — RETRIEVE-강등은 trace 에서 정상 검색과 구분 안 되므로 필수.
    """

    async def test_unmappable_action_degrades_to_retrieve_with_breadcrumb(self):
        bad = IntakeOutput(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)
        # _ACTION_MAP 에 없는 값으로 action 을 변조 → _build_update 의 action 폴백 유도.
        object.__setattr__(bad, "action", "FABRICATED_ACTION")
        node = _nodes(_intake_returning(bad))
        update = await node.intake_node(make_agent_state(message="x"))
        # RETRIEVE 로 강등 + 조작 ID 바인딩 0.
        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["target_service_ids"] is None
        # breadcrumb: route 가 아니라 action 폴백.
        assert "intake_action_fallback" in update["node_path"]
        assert "intake_route_fallback" not in update["node_path"]

    async def test_action_fallback_logs_warning(self):
        # silent no-op 금지 — logger.warning 이 호출되는지 직접 관측(caplog 전파
        # 설정에 의존하지 않도록 모듈 로거를 patch 해 결정적으로 검증).
        bad = IntakeOutput(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)
        object.__setattr__(bad, "action", "FABRICATED_ACTION")
        node = _nodes(_intake_returning(bad))
        with patch("agents.nodes.intake.logger.warning") as warn:
            await node.intake_node(make_agent_state(message="x"))
        assert any(
            "intake_action_fallback" in str(call.args[0]) for call in warn.call_args_list
        )


class TestDecisionLabelExposure:
    """in-range 오선택 완화 — decision 이벤트에 선택 라벨 노출(soft 오선택 투명화).

    검색 스킵 경로(DRILL)에서 _emit_intake 가 decision 을 단일 발행하며, 바인딩된
    service_id 의 라벨을 '(선택: 〈라벨〉)' 로 붙여 노출하는지.
    """

    async def test_drill_decision_includes_selected_label(self):
        node = _nodes(
            make_intake(
                turn_kind=TurnKind.DRILL,
                ref_indices=[2],
                user_rationale="두 번째 항목을 안내합니다",
            )
        )
        state = make_agent_state(message="두 번째 자세히", prev_entities=_PREV)
        captured: dict = {}

        def _capture(action, routes, rationale):
            captured["rationale"] = rationale

        # _emit_intake 는 호출부에서 from agents._helpers import emit_decision 한다.
        with patch("agents._helpers.emit_decision", side_effect=_capture):
            update = await node.intake_node(state)
        # decision 단일 발행 가드 세팅 + 선택 라벨 노출.
        assert update["emit"]["decision_emitted"] is True
        assert "두 번째 항목을 안내합니다" in captured["rationale"]
        assert "선택:" in captured["rationale"]
        assert "한강 자연 관찰" in captured["rationale"]  # S2 의 라벨

    async def test_retrieve_defers_decision_to_router(self):
        # NEW+RETRIEVE 는 router_node 가 routes 확정 후 emit → intake 는 decision 미발행.
        node = _nodes(
            make_intake(
                turn_kind=TurnKind.NEW,
                action=IntakeAction.RETRIEVE,
                user_rationale="검색합니다",
            )
        )
        with patch("agents._helpers.emit_decision") as emit:
            update = await node.intake_node(make_agent_state(message="수영장"))
        emit.assert_not_called()
        assert "decision_emitted" not in update.get("emit", {})


class TestAdversarialIndexType:
    """조작 ID 바인딩 0 — LLM 이 비-int 인덱스(타입 환각)를 줘도 안전.

    resolve_ref_indices 의 isinstance(idx, int) 가드가 _build_update 경로에서도
    가짜 service_id 를 만들지 않고 NEW 로 강등하는지(적대적).
    """

    async def test_non_int_index_does_not_bind(self):
        bad = IntakeOutput(turn_kind=TurnKind.DRILL, action=IntakeAction.RETRIEVE)
        # ref_indices 를 문자열/실수 섞인 환각 값으로 변조.
        object.__setattr__(bad, "ref_indices", ["2", 2.0, None])
        node = _nodes(_intake_returning(bad))
        state = make_agent_state(message="두 번째", prev_entities=_PREV)
        update = await node.intake_node(state)
        # 비-int 는 전부 드롭 → 바인딩 0 → NEW 폴백(환각 ID 0).
        assert update["target_service_ids"] is None
        assert update["triage"]["turn_kind"] == "NEW"
        assert "intake_route_fallback" in update["node_path"]


class TestRouteIntakeBranches:
    """route_intake 각 분기 도달(직접 단위) — 그래프 E2E 없이 분기 매핑 확인."""

    def _route(self, *, turn_kind=None, action=None, error=None, answer=""):
        node = _nodes(make_intake())
        state = make_agent_state(message="x")
        if turn_kind is not None:
            state["triage"]["turn_kind"] = turn_kind
        if action is not None:
            state["triage"]["action"] = action
        if error is not None:
            state["error"] = error
        if answer:
            state["output"]["answer"] = answer
        return node.route_intake(state)

    def test_refine_to_working_set_refine(self):
        assert self._route(turn_kind="REFINE") == "working_set_refine_node"

    def test_drill_to_rehydrate(self):
        assert self._route(turn_kind="DRILL") == "rehydrate_node"

    def test_relevance_to_rehydrate(self):
        assert self._route(turn_kind="RELEVANCE") == "rehydrate_node"

    def test_meta_to_explain(self):
        assert self._route(turn_kind="META") == "explain_node"

    def test_new_retrieve_to_router(self):
        assert (
            self._route(turn_kind="NEW", action=ActionType.RETRIEVE) == "router_node"
        )

    def test_new_direct_answer(self):
        assert (
            self._route(turn_kind="NEW", action=ActionType.DIRECT_ANSWER)
            == "direct_answer_node"
        )

    def test_new_ambiguous(self):
        assert (
            self._route(turn_kind="NEW", action=ActionType.AMBIGUOUS)
            == "ambiguous_node"
        )

    def test_new_out_of_scope(self):
        assert (
            self._route(turn_kind="NEW", action=ActionType.OUT_OF_SCOPE)
            == "out_of_scope_node"
        )

    def test_error_with_answer_routes_to_answer_node(self):
        # (B) 노드 예외(error + answer 세팅) → answer_node 직행.
        assert (
            self._route(error="boom", answer="안내드립니다", action=ActionType.RETRIEVE)
            == "answer_node"
        )

    def test_unhandled_turn_kind_converges_to_router(self):
        # should-never-happen 방어: 미지 turn_kind + action 없음 → router_node 수렴.
        assert self._route(turn_kind="MYSTERY", action=None) == "router_node"
