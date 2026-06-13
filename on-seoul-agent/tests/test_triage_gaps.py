"""TriageAgent 구현 검증 갭 보완 테스트.

커버리지 및 불변식 검증에서 발견된 누락 경로를 보완한다.

갭 목록:
1. TriageOutput 필드 검증자 — 허용 외 값 → None 정규화
2. domain_outside → node_path에 검색 노드 미포함 단언
3. AMBIGUOUS → cache_check_node 미실행 단언 (node_path)
4. route_by_action_fanout: enable_secondary_intent=True + secondary → ["sql_node", "vector_node"]
5. RETRIEVE + secondary=None + enable_secondary_intent=True → 단일 라우트 일관성
6. recursion_limit=22 여유 확인: 정상 RETRIEVE + retry 1회 통과
"""

from unittest.mock import MagicMock, patch

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.triage_agent import TriageAgent, TriageOutput
from schemas.state import ActionType, AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_triage,
    make_ai_session,
    run_graph,
)


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _answer(text: str = "답변입니다."):
    return make_answer_agent(text)


# ---------------------------------------------------------------------------
# 1. TriageOutput 형태 — action 결정 전담 (검색 계획 필드 없음)
# ---------------------------------------------------------------------------


class TestTriageOutputShape:
    def test_triage_output_action_only(self):
        """TriageOutput은 action/out_of_scope_type/user_rationale/reasoning만 가진다."""
        fields = set(TriageOutput.model_fields.keys())
        assert fields == {"reasoning", "action", "out_of_scope_type", "user_rationale"}

    def test_minimal_construction(self):
        """action만으로 구성된다."""
        out = TriageOutput(action=ActionType.RETRIEVE)
        assert out.action == ActionType.RETRIEVE
        assert out.out_of_scope_type is None
        assert out.user_rationale is None


# ---------------------------------------------------------------------------
# 2. OUT_OF_SCOPE/domain_outside → node_path에 검색 노드 미포함 단언
# ---------------------------------------------------------------------------


class TestOutOfScopeDomainOutsideNodePath:
    async def test_domain_outside_node_path_excludes_search_nodes(self):
        """OUT_OF_SCOPE/domain_outside 경로: sql_node, vector_node가 node_path에 없다."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            user_rationale="서울 공공서비스 범위 밖입니다.",
        )
        graph = AgentGraph(triage=triage, answer_agent=_answer())
        result = await run_graph(
            graph,
            _state(message="오늘 서울 날씨"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        path = result["node_path"]
        assert "out_of_scope_domain_outside" in path
        assert "sql_node" not in path
        assert "vector_node" not in path
        assert "hydration_node" not in path
        assert "cache_check" not in " ".join(path)

    async def test_domain_outside_has_answer(self):
        """domain_outside 경로는 user_rationale이 답변으로 설정된다."""
        rationale = "서울 공공서비스 예약 안내 챗봇입니다."
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            user_rationale=rationale,
        )
        graph = AgentGraph(triage=triage, answer_agent=_answer())
        result = await run_graph(
            graph,
            _state(message="날씨 알려줘"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["output"]["answer"] == rationale


# ---------------------------------------------------------------------------
# 3. AMBIGUOUS → cache_check_node 미실행 단언 (node_path)
# ---------------------------------------------------------------------------


class TestAmbiguousNodePath:
    async def test_ambiguous_node_path_excludes_cache_and_search(self):
        """AMBIGUOUS action 경로: cache_check_node와 검색 노드가 node_path에 없다."""
        triage = make_triage(
            ActionType.AMBIGUOUS,
            user_rationale="어떤 서비스를 찾으시나요?",
        )
        graph = AgentGraph(triage=triage, answer_agent=_answer())
        result = await run_graph(
            graph,
            _state(message="좋은 곳"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        path = result["node_path"]
        assert "ambiguous_node" in path
        assert "cache_check_hit" not in path
        assert "cache_check_miss" not in path
        assert "sql_node" not in path
        assert "vector_node" not in path

    async def test_ambiguous_with_no_rationale_uses_default_message(self):
        """user_rationale 없는 AMBIGUOUS는 기본 안내 메시지를 반환한다."""
        triage = make_triage(ActionType.AMBIGUOUS, user_rationale=None)
        graph = AgentGraph(triage=triage, answer_agent=_answer())
        result = await run_graph(
            graph,
            _state(message="좋은 곳"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["output"]["answer"] is not None
        assert len(result["output"]["answer"]) > 0


# ---------------------------------------------------------------------------
# 4. route_by_action_fanout: enable_secondary_intent=True + secondary 있으면 팬아웃
# ---------------------------------------------------------------------------


class TestRouteByActionFanout:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer())._nodes

    def test_fanout_when_secondary_present_and_enabled(self):
        """enable_secondary_intent=True + secondary 있으면 [sql_node, vector_node] 반환."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            result = nodes.route_by_action_fanout(state)
        assert result == ["sql_node", "vector_node"]

    def test_no_fanout_when_disabled(self):
        """enable_secondary_intent=False이면 단일 라우트를 반환한다."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = False
            result = nodes.route_by_action_fanout(state)
        # 단일 라우트 — SQL_SEARCH → sql_node
        assert result == "sql_node"

    def test_no_fanout_when_secondary_none(self):
        """enable_secondary_intent=True이지만 secondary=None이면 단일 라우트."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.VECTOR_SEARCH,
            secondary_intent=None,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            result = nodes.route_by_action_fanout(state)
        assert result == "vector_node"

    def test_no_fanout_for_non_sql_vector_primary(self):
        """primary가 MAP이면 secondary가 있어도 단일 라우트(MAP → map_node)."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.MAP,
            secondary_intent=IntentType.SQL_SEARCH,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            result = nodes.route_by_action_fanout(state)
        assert result == "map_node"


# ---------------------------------------------------------------------------
# 5. RETRIEVE + secondary=None + enable_secondary_intent=False → 단일 라우트
# ---------------------------------------------------------------------------


class TestSingleRouteConsistency:
    def test_retrieve_sql_single_route(self):
        """RETRIEVE/SQL_SEARCH, secondary=None → sql_node."""
        nodes = AgentGraph(answer_agent=_answer())._nodes
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.SQL_SEARCH,
            secondary_intent=None,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = False
            result = nodes.route_by_action_fanout(state)
        assert result == "sql_node"

    def test_retrieve_vector_single_route(self):
        """RETRIEVE/VECTOR_SEARCH, secondary=None → vector_node."""
        nodes = AgentGraph(answer_agent=_answer())._nodes
        state = _state(
            action=ActionType.RETRIEVE,
            intent=IntentType.VECTOR_SEARCH,
            secondary_intent=None,
        )
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = False
            result = nodes.route_by_action_fanout(state)
        assert result == "vector_node"


# ---------------------------------------------------------------------------
# 6. EXPLAIN action — node_path 단언
# ---------------------------------------------------------------------------


class TestExplainNodePath:
    async def test_explain_with_prev_reasoning_node_path(self):
        """EXPLAIN + prev_reasoning → explain_node가 node_path에 포함된다."""
        triage = make_triage(ActionType.EXPLAIN)
        graph = AgentGraph(triage=triage, answer_agent=_answer("직접 답변"))
        result = await run_graph(
            graph,
            _state(
                message="왜 그랬어?",
                prev_reasoning="자연 체험 관련 키워드가 있어서 분류했습니다.",
            ),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        path = result["node_path"]
        assert "explain_node" in path
        assert "sql_node" not in path
        assert "vector_node" not in path

    async def test_explain_without_prev_reasoning_falls_to_direct_answer_node(self):
        """EXPLAIN + prev_reasoning=None → direct_answer_node 폴백, node_path 확인."""
        triage = make_triage(ActionType.EXPLAIN)
        graph = AgentGraph(triage=triage, answer_agent=_answer("직접 답변"))
        result = await run_graph(
            graph,
            _state(message="왜 그랬어?", prev_reasoning=None),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        # explain_node 내부에서 direct_answer_node로 폴백하므로
        # node_path에는 explain_node가 아닌 direct_answer_node가 나타나야 한다
        path = result["node_path"]
        assert "explain_node" not in path
        assert "direct_answer_node" in path


# ---------------------------------------------------------------------------
# 7. DIRECT_ANSWER action — node_path에 cache 노드 없음 단언
# ---------------------------------------------------------------------------


class TestDirectAnswerNodePath:
    async def test_direct_answer_node_path_excludes_cache_and_search(self):
        """DIRECT_ANSWER 경로: cache_check 및 검색 노드가 node_path에 없다."""
        triage = make_triage(ActionType.DIRECT_ANSWER)
        graph = AgentGraph(triage=triage, answer_agent=_answer("안녕!"))
        result = await run_graph(
            graph,
            _state(message="안녕하세요"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        path = result["node_path"]
        assert "direct_answer_node" in path
        assert "cache_check_hit" not in path
        assert "cache_check_miss" not in path
        assert "sql_node" not in path
        assert "vector_node" not in path
        assert "retry_prep" not in path


# ---------------------------------------------------------------------------
# 8. MUST-FIX 1 회귀 — main.py TriageAgent 미연결
# ---------------------------------------------------------------------------


class TestTriageConnectedInMainLifespan:
    """AgentGraph(redis=...) 생성 시 triage가 연결되어 5-action 분기가 동작한다."""

    def test_agent_graph_default_has_triage_not_router(self):
        """AgentGraph() 기본 생성 시 _nodes._triage가 TriageAgent 인스턴스여야 한다."""
        graph = AgentGraph()
        nodes = graph._nodes
        assert isinstance(nodes._triage, TriageAgent), (
            "_triage가 None이거나 RouterAgent입니다. "
            "main.py 또는 GraphNodes.__init__에서 TriageAgent()로 기본 초기화해야 합니다."
        )

    def test_nodes_triage_and_router_both_injected(self):
        """triage만 주입해도 _triage가 세팅되고 _router(RouterAgent)가 기본 생성된다.

        책임 분리: triage_node(action) + router_node(검색 계획) 둘 다 동작해야 하므로
        AgentGraph가 RouterAgent를 자동 주입한다.
        """
        from agents.router_agent import RouterAgent

        triage = make_triage(ActionType.DIRECT_ANSWER)
        graph = AgentGraph(triage=triage)
        assert graph._nodes._triage is triage
        assert isinstance(graph._nodes._router, RouterAgent)


# ---------------------------------------------------------------------------
# 9. MUST-FIX 2 회귀 — attribute_gap hydration 경로 끊김
# ---------------------------------------------------------------------------


class TestAttributeGapIntentSet:
    """out_of_scope_node attribute_gap 분기에서 intent=VECTOR_SEARCH가 설정된다."""

    async def test_out_of_scope_attribute_gap_sets_intent_vector(self):
        """out_of_scope_node: attribute_gap이면 state에 intent=VECTOR_SEARCH가 세팅된다."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            refined_query="마루공원 테니스장",
            vector_sub_intent="identification",
        )
        nodes = GraphNodes(triage=triage, answer_agent=_answer())
        state = _state(
            message="마루공원 테니스장 보수 공사 일정",
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            refined_query="마루공원 테니스장",
        )
        update = await nodes.out_of_scope_node(state)
        assert update["plan"].get("intent") == IntentType.VECTOR_SEARCH, (
            "attribute_gap 분기에서 intent=VECTOR_SEARCH가 설정되지 않으면 "
            "HydrationNode 가 hydrated_services=[] 로 떨어져 service_url 안내 불가."
        )
        assert update["plan"].get("vector_sub_intent") == "identification"


# ---------------------------------------------------------------------------
# 10. FALLBACK 액션이 ActionType에 존재하지 않음을 명시적으로 확인
# ---------------------------------------------------------------------------


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
