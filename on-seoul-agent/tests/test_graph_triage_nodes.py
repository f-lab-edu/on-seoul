"""TriageAgent 통합 테스트 — 노드 단위 검증.

검증 대상:
- pre-answer 0건 게이트
- RRF fusion 노드 (enable_secondary_intent / secondary 유무)
- intake_node 슬롯 전파 (action/turn_kind/oos)
- router_node 슬롯 전파 (intent/plan/filters)
"""

from unittest.mock import AsyncMock, patch


from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from core.config import settings
from schemas.intake import IntakeAction, TurnKind
from schemas.state import ActionType, IntentType
from tests._graph_triage_support import _answer_agent, _state
from tests.helpers import (
    make_intake,
    make_router,
    make_sql_agent,
    make_ai_session,
    patch_node_sessions,
    run_graph,
)


# ---------------------------------------------------------------------------
# 3. pre-answer 0건 게이트
# ---------------------------------------------------------------------------


class TestPreAnswerGate:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer_agent())._nodes

    def test_zero_hydrated_triggers_retry_prep(self):
        """hydrated_services=[] 이면 retry_prep_node로 라우팅된다."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            hydrated_services=[],
            retry_count=0,
        )
        assert nodes.route_pre_answer_gate(state) == "retry_prep_node"

    def test_non_empty_hydrated_reaches_answer(self):
        """hydrated_services 유건이면 answer_node로 라우팅된다."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_none_hydrated_reaches_answer(self):
        """hydrated_services=None (미설정)이면 answer_node로 통과한다."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE, hydrated_services=None, retry_count=0
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_zero_hydrated_capped_after_retry(self):
        """0건이지만 retry_count>=1이면 answer_node로 통과 (무한루프 방지)."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            hydrated_services=[],
            retry_count=1,
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_non_retrieve_action_passes_through(self):
        """비-RETRIEVE action은 게이트 통과 -> answer_node."""
        nodes = self._nodes()
        for action in (ActionType.DIRECT_ANSWER, ActionType.AMBIGUOUS):
            state = _state(action=action, hydrated_services=[], retry_count=0)
            assert nodes.route_pre_answer_gate(state) == "answer_node"

    async def test_c2_gate_prevents_answer_llm_on_zero_hits(self):
        """0건 게이트: 0건 시 answer_node LLM 미호출 + retry_prep 직행 E2E."""
        intake = make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])  # SQL 0건
        answer_agent = _answer_agent("재시도 후 답변")

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        assert "pre_answer_gate" in path
        # 1차 answer_node는 0건으로 미호출 -> retry_prep -> 2차 실행
        # retry_count=1이면 2차 게이트에서 answer로 통과
        assert "retry_prep" in path
        assert result["retry_count"] == 1


# ---------------------------------------------------------------------------
# 5. RRF fusion 노드
# ---------------------------------------------------------------------------


class TestRRFFusionNode:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer_agent())._nodes

    async def test_rrf_fusion_bypasses_when_disabled(self):
        """enable_secondary_intent=False이면 rrf_fusion_node가 bypass된다."""
        nodes = self._nodes()
        state = _state(
            secondary_intent=IntentType.VECTOR_SEARCH,
            sql_results=[{"service_id": "S1"}],
            vector_results=[{"service_id": "V1"}],
        )
        with (
            patch.object(settings, "enable_secondary_intent", False),
            patch.object(settings, "rrf_k_constant", 60),
            patch.object(settings, "rrf_top_k_final", 10),
        ):
            result = await nodes.rrf_fusion_node(state)
        assert "rrf_fusion_bypass" in result.get("node_path", [])
        assert "rrf_merged_ids" not in result

    async def test_rrf_fusion_bypasses_when_no_secondary(self):
        """secondary_intent=None이면 bypass된다."""
        nodes = self._nodes()
        state = _state(
            secondary_intent=None,
            sql_results=[{"service_id": "S1"}],
            vector_results=[{"service_id": "V1"}],
        )
        with (
            patch.object(settings, "enable_secondary_intent", True),
            patch.object(settings, "rrf_k_constant", 60),
            patch.object(settings, "rrf_top_k_final", 10),
        ):
            result = await nodes.rrf_fusion_node(state)
        assert "rrf_fusion_bypass" in result.get("node_path", [])

    async def test_rrf_fusion_merges_sql_and_vector(self):
        """enable_secondary_intent=True + secondary 있으면 RRF 통합을 수행한다."""
        nodes = self._nodes()
        state = _state(
            secondary_intent=IntentType.VECTOR_SEARCH,
            sql_results=[
                {"service_id": "S1"},
                {"service_id": "S2"},
            ],
            vector_results=[
                {"service_id": "V1"},
                {"service_id": "S1"},  # SQL과 겹치는 결과
            ],
        )
        with (
            patch.object(settings, "enable_secondary_intent", True),
            patch.object(settings, "rrf_k_constant", 60),
            patch.object(settings, "rrf_top_k_final", 10),
        ):
            result = await nodes.rrf_fusion_node(state)

        assert "rrf_merged_ids" in result
        merged = result["rrf_merged_ids"]
        assert len(merged) > 0
        # S1은 SQL과 VECTOR 양쪽에 등장하므로 상위 랭킹이어야 한다
        assert merged[0] == "S1"

    async def test_rrf_fusion_empty_channels_returns_empty_path(self):
        """두 채널 모두 0건이면 rrf_fusion_empty 경로."""
        nodes = self._nodes()
        state = _state(
            secondary_intent=IntentType.VECTOR_SEARCH,
            sql_results=[],
            vector_results=[],
        )
        with (
            patch.object(settings, "enable_secondary_intent", True),
            patch.object(settings, "rrf_k_constant", 60),
            patch.object(settings, "rrf_top_k_final", 10),
        ):
            result = await nodes.rrf_fusion_node(state)
        assert "rrf_fusion_empty" in result.get("node_path", [])


# ---------------------------------------------------------------------------
# 6. triage_node 슬롯 전파
# ---------------------------------------------------------------------------


class TestTriageNodeStatePropagation:
    """입구 단일화 후: intake_node 가 triage 채널(action/turn_kind/oos)을 채운다."""

    async def test_intake_node_sets_action_only(self):
        """intake_node는 action/turn_kind/user_rationale만 채운다(검색 계획 제외)."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="마포구 풋살장 검색",
        )
        nodes = GraphNodes(intake=intake, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.intake_node(_state(message="마포구 풋살장"))

        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["triage"]["user_rationale"] == "마포구 풋살장 검색"
        # 검색 계획은 router_node 책임 — intake_node update에 없어야 한다.
        assert "intent" not in update
        assert "secondary_intent" not in update
        assert "refined_query" not in update

    async def test_intake_node_does_not_honor_forced_intent(self):
        """forced_intent honor는 router_node 책임 — intake_node는 무시한다."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="검색",
        )
        nodes = GraphNodes(intake=intake, answer_agent=_answer_agent())

        structured = intake._llm.with_structured_output.return_value
        with patch_node_sessions():
            update = await nodes.intake_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )

        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert "intent" not in update
        structured.ainvoke.assert_called_once()

    async def test_intake_node_out_of_scope_slots(self):
        """intake_node: NEW+OUT_OF_SCOPE면 out_of_scope_type이 state에 채워진다."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.OUT_OF_SCOPE,
            oos_type="domain_outside",
            user_rationale="범위 밖입니다.",
        )
        nodes = GraphNodes(intake=intake, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.intake_node(_state())

        assert update["triage"]["action"] == ActionType.OUT_OF_SCOPE
        assert update["triage"]["out_of_scope_type"] == "domain_outside"
        assert update["triage"]["user_rationale"] == "범위 밖입니다."


class TestRouterNodeStatePropagation:
    async def test_router_node_sets_intent_and_plan(self):
        """router_node가 intent/refined_query/post-filter/secondary_intent를 채운다."""
        router = make_router(
            IntentType.SQL_SEARCH,
            refined_query="마포구 풋살장",
            max_class_name="체육시설",
            area_name="마포구",
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        nodes = GraphNodes(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
        )
        with patch_node_sessions():
            update = await nodes.router_node(_state(message="마포구 풋살장"))

        assert update["plan"]["intent"] == IntentType.SQL_SEARCH
        assert update["plan"]["refined_query"] == "마포구 풋살장"
        assert update["filters"]["max_class_name"] == "체육시설"
        assert update["filters"]["area_name"] == "마포구"
        assert update["plan"]["secondary_intent"] == IntentType.VECTOR_SEARCH

    async def test_router_node_omits_none_fields(self):
        """router_node: None 필드는 update에 포함하지 않는다."""
        router = make_router(IntentType.VECTOR_SEARCH)
        nodes = GraphNodes(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
        )
        with patch_node_sessions():
            update = await nodes.router_node(_state())
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert "secondary_intent" not in update
        assert "max_class_name" not in update

    async def test_router_node_honors_forced_intent(self):
        """forced_intent가 있으면 LLM 미호출하고 그 intent를 그대로 반환한다(triage에서 이관)."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = GraphNodes(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
        )
        structured = router._llm.with_structured_output.return_value
        with patch_node_sessions():
            update = await nodes.router_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert update["forced_intent"] is None
        structured.ainvoke.assert_not_called()
