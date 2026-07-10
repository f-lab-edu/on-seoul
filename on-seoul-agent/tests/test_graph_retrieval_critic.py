"""L1 retrieval-critic 그래프 배선 테스트 (escalation 게이트).

가장 위험한 단계(라우팅 변경)라 회귀 안전이 최우선이다. 이 파일은:

  - escalation 게이트 판정: 명백히 좋음 → answer 직행(critic 미호출 = 80% 경로 보존),
    의심스러움(0건/thin/skew) → retrieval_critic_node.
  - route_critic 조건부 엣지: ANSWER→answer / REPLAN→retry_prep / STOP→answer,
    critic 미결정(fail-open None) → 결정적 폴백.
  - retry_prep_node 의 critic_replan_hint 소비(intent 전환/필터 드롭/재구성) +
    소비 후 1회성 클리어 + 폴백 층(힌트 없으면 결정적 규칙).
  - 예산/루프 경계: max_retrieval_retries 소진 시 하드 백스톱(critic 미진입).
  - 플래그 오프(기본): 기존 결정적 경로만 — 회귀 0.

모든 LLM 은 fake(structured-output mock)로 주입한다 — 실 호출 금지.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.retrieval_critic import RetrievalCritic
from core.config import settings
from schemas.critic import CriticOutput, ReplanHint
from schemas.state import IntentType
from tests.helpers import (
    make_answer_agent,
    make_intake_router,
    run_graph,
    make_agent_state,
)
from tests._graph_support import _ai_session, _sql_agent, _vector_agent


def _make_critic(output: CriticOutput | None = None, *, raise_exc=None) -> RetrievalCritic:
    """고정 CriticOutput(또는 예외/미결정)을 반환하는 fake structured-output critic."""
    critic = RetrievalCritic.__new__(RetrievalCritic)
    structured = MagicMock()
    if raise_exc is not None:
        structured.ainvoke = AsyncMock(side_effect=raise_exc)
    else:
        structured.ainvoke = AsyncMock(return_value=output)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    critic._llm = llm
    return critic


def _state(**kwargs):
    return make_agent_state(**kwargs)


# ---------------------------------------------------------------------------
# 1. escalation 게이트 판정 (route_pre_answer_gate 확장)
# ---------------------------------------------------------------------------


class TestEscalationGate:
    """pre_answer_gate 후단 결정적 triage — 의심스러울 때만 critic 진입."""

    def _nodes(self, *, critic=None) -> GraphNodes:
        return AgentGraph(answer_agent=make_answer_agent(), critic=critic)._nodes

    def test_flag_off_preserves_deterministic_zero_hit_route(self):
        """플래그 오프(기본): 0건 → 기존 retry_prep 직행(critic 미진입, 회귀 0)."""
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],
            retry_count=0,
        )
        with patch.object(settings, "enable_retrieval_critic", False):
            assert nodes.route_pre_answer_gate(state) == "retry_prep_node"

    def test_flag_off_good_result_routes_answer(self):
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
        )
        with patch.object(settings, "enable_retrieval_critic", False):
            assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_clearly_good_result_bypasses_critic(self):
        """명백히 좋은 결과(유건·비thin·비skew) → answer 직행(critic 미호출, 80% 경로)."""
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": f"S{i}"} for i in range(5)],
            retry_count=0,
            result_quality=None,
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_zero_hit_escalates_to_critic(self):
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],
            retry_count=0,
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "retrieval_critic_node"

    def test_thin_result_escalates_to_critic(self):
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
            result_quality={"thin": True, "skew_field": None},
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "retrieval_critic_node"

    def test_skew_result_escalates_to_critic(self):
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": f"S{i}"} for i in range(4)],
            retry_count=0,
            result_quality={
                "thin": False,
                "skew_field": "area_name",
                "skew_value": "강남구",
                "skew_ratio": 0.9,
            },
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "retrieval_critic_node"

    def test_budget_exhausted_hard_backstop_no_critic(self):
        """예산 소진(retry_count >= max) → 하드 백스톱: critic 미진입, answer 직행."""
        nodes = self._nodes(critic=_make_critic())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],
            retry_count=settings.max_retrieval_retries,
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_flag_on_but_no_critic_falls_back_deterministic(self):
        """플래그 온이지만 critic 미주입 → 결정적 경로(0건→retry) 폴백(fail-open).

        AgentGraph 는 항상 critic 을 기본 생성하므로 critic=None 경로는 GraphNodes 를
        직접 생성해 검증한다(게이트의 `self._critic is not None` 가드 확인).
        """
        nodes = GraphNodes(answer_agent=make_answer_agent(), critic=None)
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],
            retry_count=0,
        )
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "retry_prep_node"

    def test_non_search_path_answers_directly(self):
        nodes = self._nodes(critic=_make_critic())
        from schemas.state import ActionType

        state = _state(hydrated_services=[])
        state["triage"]["action"] = ActionType.DIRECT_ANSWER
        with patch.object(settings, "enable_retrieval_critic", True):
            assert nodes.route_pre_answer_gate(state) == "answer_node"


# ---------------------------------------------------------------------------
# 2. route_critic 조건부 엣지 (ANSWER/REPLAN/STOP + fail-open 폴백)
# ---------------------------------------------------------------------------


class TestRouteCritic:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=make_answer_agent(), critic=_make_critic())._nodes

    def test_answer_decision_routes_answer(self):
        nodes = self._nodes()
        assert nodes.route_critic(_state(critic_decision="ANSWER")) == "answer_node"

    def test_replan_decision_routes_retry_prep(self):
        nodes = self._nodes()
        assert (
            nodes.route_critic(_state(critic_decision="REPLAN")) == "retry_prep_node"
        )

    def test_stop_decision_routes_answer(self):
        nodes = self._nodes()
        assert nodes.route_critic(_state(critic_decision="STOP")) == "answer_node"

    def test_none_decision_fail_open_deterministic_zero_hit_retry(self):
        """critic 미결정(None) + 0건 → 결정적 폴백(retry_prep)."""
        nodes = self._nodes()
        state = _state(
            critic_decision=None,
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[],
            retry_count=0,
        )
        assert nodes.route_critic(state) == "retry_prep_node"

    def test_none_decision_fail_open_deterministic_with_hits_answer(self):
        """critic 미결정(None) + 유건 → 결정적 폴백(answer)."""
        nodes = self._nodes()
        state = _state(
            critic_decision=None,
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
        )
        assert nodes.route_critic(state) == "answer_node"

    def test_replan_but_budget_exhausted_routes_answer(self):
        """REPLAN 이라도 예산 소진이면 재시도 불가 → answer 직행(하드 백스톱)."""
        nodes = self._nodes()
        state = _state(
            critic_decision="REPLAN",
            retry_count=settings.max_retrieval_retries,
        )
        assert nodes.route_critic(state) == "answer_node"

    async def test_critic_node_without_critic_fail_open(self):
        """critic 미주입인데 노드에 도달하면(방어) 세 슬롯 None + no_critic breadcrumb.

        정상 경로에선 게이트가 진입을 차단하지만, 방어 분기(retrieval.py:361)를 직접
        검증한다 — 노드가 예외 없이 fail-open dict 를 반환해 route_critic 이 폴백한다.
        """
        nodes = GraphNodes(answer_agent=make_answer_agent(), critic=None)
        result = await nodes.retrieval_critic_node(_state())
        assert result["critic_decision"] is None
        assert result["critic_replan_hint"] is None
        assert result["critic_rationale"] is None
        assert result["node_path"] == ["retrieval_critic:no_critic"]


# ---------------------------------------------------------------------------
# 3. retry_prep_node 의 critic_replan_hint 소비 + 폴백 층
# ---------------------------------------------------------------------------


class TestRetryPrepCriticHint:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=make_answer_agent(), critic=_make_critic())._nodes

    async def test_consumes_intent_switch_hint(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            area_name="강남구",
            critic_replan_hint={
                "intent": IntentType.VECTOR_SEARCH.value,
                "drop_filters": None,
                "reformulate_query": None,
                "reason": "정형 필터 과함",
            },
        )
        result = await nodes.retry_prep_node(state)
        assert result["forced_intent"] == IntentType.VECTOR_SEARCH
        assert result["retry_count"] == 1
        # breadcrumb 에 critic 사유가 남는다.
        assert "retry_prep:critic" in result["node_path"]

    async def test_consumes_drop_filters_hint(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            area_name="강남구",
            service_status="접수중",
            critic_replan_hint={
                "intent": None,
                "drop_filters": ["area_name"],
                "reformulate_query": None,
                "reason": "지역 완화",
            },
        )
        result = await nodes.retry_prep_node(state)
        # area_name 만 드롭, service_status 는 유지.
        assert result["filters"]["area_name"] is None
        assert "service_status" not in result["filters"]

    async def test_consumes_reformulate_query_hint(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.VECTOR_SEARCH,
            retry_count=0,
            critic_replan_hint={
                "intent": None,
                "drop_filters": None,
                "reformulate_query": "실내 수영장",
                "reason": "재구성",
            },
        )
        result = await nodes.retry_prep_node(state)
        assert result["plan"]["refined_query"] == "실내 수영장"

    async def test_clears_hint_after_consumption(self):
        """critic 힌트는 1회성 — 소비 후 None 으로 클리어한다."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            critic_replan_hint={
                "intent": IntentType.VECTOR_SEARCH.value,
                "reason": "x",
            },
        )
        result = await nodes.retry_prep_node(state)
        assert result["critic_decision"] is None
        assert result["critic_replan_hint"] is None
        assert result["critic_rationale"] is None

    async def test_no_hint_falls_back_to_deterministic(self):
        """critic 힌트 없으면 기존 결정적 규칙(SQL→VECTOR 강제) — 폴백 층."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            area_name="강남구",
            critic_replan_hint=None,
        )
        result = await nodes.retry_prep_node(state)
        # 기존 결정적 전환 규칙 유지(SQL→VECTOR).
        assert result["forced_intent"] == IntentType.VECTOR_SEARCH
        assert "retry_prep:critic" not in result["node_path"]

    async def test_invalid_intent_hint_ignored_injection_guard(self):
        """힌트 intent 가 유효 enum 이 아니면 무시(인젝션 가드, correction.py:267-268).

        스키마 밖 자유 식별자가 힌트로 흘러들어도 forced_intent 로 승격되지 않는다.
        intent 만 실린 힌트라 다른 방향도 없으면 실효 힌트 없음 → 전체 리셋 폴백.
        """
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            area_name="강남구",
            critic_replan_hint={
                "intent": "DROP TABLE services",  # 유효 enum 아님
                "drop_filters": None,
                "reformulate_query": None,
                "reason": "injection",
            },
        )
        result = await nodes.retry_prep_node(state)
        # 무효 intent 는 forced_intent 로 승격되지 않는다.
        assert result.get("forced_intent") is None
        # critic 경로로 진입은 했으나(breadcrumb) 실효 힌트 없음 → 전체 리셋 폴백.
        assert "retry_prep:critic" in result["node_path"]
        assert result["filters"]["area_name"] is None

    async def test_ineffective_hint_falls_back_to_full_reset(self):
        """힌트는 있으나 실효 필드가 하나도 없으면 결정적 전체 리셋(correction.py:304-320).

        drop_filters 가 화이트리스트 밖(또는 미적용 필터)이라 valid_drops 가 비고,
        intent/reformulate 도 없으면 applied=False → 전체 리셋 폴백.
        """
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            area_name="강남구",
            critic_replan_hint={
                "intent": None,
                # 화이트리스트 밖 필터명 — valid_drops 에서 걸러진다.
                "drop_filters": ["nonexistent_filter"],
                "reformulate_query": None,
                "reason": "무효 드롭",
            },
        )
        result = await nodes.retry_prep_node(state)
        assert "retry_prep:critic" in result["node_path"]
        # 실효 없음 → 전체 리셋: 모든 필터 None + plan.refined_query None.
        assert result["filters"]["area_name"] is None
        assert result["filters"]["service_status"] is None
        assert result["plan"]["refined_query"] is None
        # 1회성 클리어도 유지.
        assert result["critic_replan_hint"] is None


# ---------------------------------------------------------------------------
# 4. E2E — critic REPLAN 사이클 / STOP / 미진입(80% 경로)
# ---------------------------------------------------------------------------


class TestCriticE2E:
    async def test_good_result_e2e_no_critic_call(self):
        """유건·명백히 좋음 → critic 미호출(node_path 에 retrieval_critic 없음)."""
        rows = [{"service_id": f"S{i}", "service_name": f"수영장{i}"} for i in range(5)]
        sql_agent, data_session = _sql_agent(rows)
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(CriticOutput(decision="ANSWER", rationale="ok"))
        hydrated = rows

        with patch(
            "agents.hydration_node.hydrate_services",
            AsyncMock(return_value=hydrated),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("안내입니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                result = await run_graph(
                    graph, _state(), data_session=data_session, ai_session=_ai_session()
                )

        assert "retrieval_critic" not in result["node_path"]
        # critic LLM 이 실제로 호출되지 않았다.
        critic._llm.with_structured_output.return_value.ainvoke.assert_not_awaited()

    async def test_replan_cycle_switches_intent_and_recovers(self):
        """0건 → critic REPLAN(intent 전환) → 재검색 → 유건 답변."""
        sql_agent, data_session = _sql_agent([])  # SQL 0건
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(
            CriticOutput(
                decision="REPLAN",
                replan_hint=ReplanHint(
                    intent=IntentType.VECTOR_SEARCH, reason="정형 실패"
                ),
                rationale="벡터로 다시 찾습니다.",
            )
        )
        # 재검색 결과는 명백히 좋음(≥3건, 비skew) → 2회차 게이트가 critic 미진입 answer 직행.
        vrows = [
            {"service_id": f"V{i}", "service_name": f"체험관{i}", "similarity": 0.8}
            for i in range(4)
        ]
        hydrated = [
            {"service_id": f"V{i}", "service_name": f"체험관{i}"} for i in range(4)
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=make_answer_agent("체험관 안내입니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                result = await run_graph(
                    graph, _state(), data_session=data_session, ai_session=ai_session
                )

        path = result["node_path"]
        assert "retrieval_critic" in path
        assert "vector_node" in path
        assert path.index("retrieval_critic") < path.index("vector_node")
        assert result["retry_count"] == 1
        assert result["output"]["answer"] == "체험관 안내입니다."

    async def test_stop_decision_answers_without_replan(self):
        """0건 → critic STOP → 재검색 없이 정직한 답변."""
        sql_agent, data_session = _sql_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(
            CriticOutput(decision="STOP", rationale="맞는 서비스가 없습니다.")
        )

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("결과가 없습니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                result = await run_graph(
                    graph, _state(), data_session=data_session, ai_session=_ai_session()
                )

        path = result["node_path"]
        assert "retrieval_critic" in path
        # STOP 은 재검색하지 않는다 — retry_prep 미진입.
        assert "retry_prep" not in path
        assert result["retry_count"] == 0

    async def test_budget_exhausted_stops_loop(self):
        """critic REPLAN 을 계속 내도 예산 소진 시 루프가 종료된다(하드 백스톱)."""
        sql_agent, data_session = _sql_agent([])
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        # 항상 REPLAN 을 내는 critic — 예산이 유일한 종료 조건.
        critic = _make_critic(
            CriticOutput(
                decision="REPLAN",
                replan_hint=ReplanHint(drop_filters=["area_name"], reason="완화"),
                rationale="계속 완화합니다.",
            )
        )

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
            ),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=make_answer_agent("최종 안내."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                result = await run_graph(
                    graph,
                    _state(area_name="강남구"),
                    data_session=data_session,
                    ai_session=ai_session,
                )

        # 예산(max_retrieval_retries)을 넘지 않고 종료된다.
        assert result["retry_count"] <= settings.max_retrieval_retries
        assert result["output"]["answer"] == "최종 안내."
