"""TriageAgent 통합 테스트 — action 라우팅 + self-correction 제외 + retry 재진입.

검증 대상:
- 5 action 라우팅 각 경로
- DIRECT_ANSWER: DB 미조회, LLM 직접 응답
- AMBIGUOUS: "좋은 곳" -> AMBIGUOUS
- OUT_OF_SCOPE/domain_outside: 사전 거절, 검색 미실행
- OUT_OF_SCOPE/attribute_gap: 엔티티 검색 -> service_url 포함
- EXPLAIN: prev_reasoning 있으면 근거 설명
- self-correction: 비-RETRIEVE action이 0건 재시도 경로 미진입
- retry → router_node 재진입 (triage 미경유) + recursion_limit 경계
"""

from unittest.mock import AsyncMock, MagicMock, patch


from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from schemas.intake import IntakeAction, TurnKind
from schemas.state import ActionType, IntentType
from tests._graph_triage_support import _answer_agent, _state
from tests.helpers import (
    make_intake,
    make_router,
    make_sql_agent,
    make_ai_session,
    run_graph,
)


# ---------------------------------------------------------------------------
# 1. 5 action 라우팅 경로
# ---------------------------------------------------------------------------


class TestTriageActionRouting:
    # RETRIEVE/SQL → sql_node → results 경로는 test_graph.TestConditionalEdgeRouting
    # .test_sql_search_route 가, triage action 슬롯 전파는 TestTriageNodeStatePropagation
    # 이 커버하는 routes-경계 중복이라 축소했다. 비검색 action 분기는 아래 유지.

    async def test_direct_answer_skips_db(self):
        """DIRECT_ANSWER action -> DB 미조회, LLM 직접 응답."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.DIRECT_ANSWER,
            user_rationale="안녕하세요!",
        )
        sql_agent, data_session = make_sql_agent([])

        graph = AgentGraph(
            intake=intake,
            sql_agent=sql_agent,
            answer_agent=_answer_agent("챗봇 안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(message="안녕하세요"),
            data_session=data_session,
            ai_session=make_ai_session(),
        )
        assert result["triage"]["action"] == ActionType.DIRECT_ANSWER
        assert result["sql"].get("results") is None
        assert result["vector"].get("results") is None
        assert result["output"]["answer"] is not None
        sql_agent._chain.ainvoke.assert_not_called()

    async def test_ambiguous_returns_clarification(self):
        """AMBIGUOUS action -> 명확화 안내 반환."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.AMBIGUOUS,
            user_rationale="어떤 종류의 시설을 찾으시나요?",
        )
        graph = AgentGraph(intake=intake, answer_agent=_answer_agent())
        result = await run_graph(
            graph,
            _state(message="좋은 곳 알려줘"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["triage"]["action"] == ActionType.AMBIGUOUS
        assert result["output"]["answer"] is not None
        assert len(result["output"]["answer"]) > 0

    async def test_out_of_scope_domain_outside_rejects(self):
        """OUT_OF_SCOPE/domain_outside -> 즉시 거절, 검색 미실행."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.OUT_OF_SCOPE,
            oos_type="domain_outside",
            user_rationale="서울 공공서비스 범위 밖입니다.",
        )
        sql_agent, data_session = make_sql_agent([])

        graph = AgentGraph(
            intake=intake, sql_agent=sql_agent, answer_agent=_answer_agent()
        )
        result = await run_graph(
            graph,
            _state(message="오늘 서울 날씨"),
            data_session=data_session,
            ai_session=make_ai_session(),
        )
        assert result["triage"]["action"] == ActionType.OUT_OF_SCOPE
        assert result["triage"]["out_of_scope_type"] == "domain_outside"
        assert (
            "범위" in result["output"]["answer"]
            or "날씨" in result["output"]["answer"]
            or result["output"]["answer"]
        )
        sql_agent._chain.ainvoke.assert_not_called()

    async def test_out_of_scope_attribute_gap_triggers_vector(self):
        """OUT_OF_SCOPE/attribute_gap -> vector_node 경유 + 시설 안내."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.OUT_OF_SCOPE,
            oos_type="attribute_gap",
        )
        vrows = [
            {
                "service_id": "V001",
                "service_name": "마루공원 테니스장",
                "similarity": 0.9,
            }
        ]
        hydrated = [
            {
                "service_id": "V001",
                "service_name": "마루공원 테니스장",
                "service_url": "https://example.com",
            }
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            from agents.vector_agent import VectorAgent, _RefinedQuery

            vector_agent = VectorAgent.__new__(VectorAgent)
            refine_chain = MagicMock()
            refine_chain.ainvoke = AsyncMock(
                return_value=_RefinedQuery(
                    refined_query="마루공원 테니스장",
                    max_class_name=None,
                    area_name=None,
                    service_status=None,
                )
            )
            vector_agent._refine_chain = refine_chain
            embeddings = MagicMock()
            embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
            vector_agent._embeddings = embeddings

            graph = AgentGraph(
                intake=intake,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("시설 페이지를 확인하세요."),
            )
            result = await run_graph(
                graph,
                _state(message="마루공원 테니스장 보수 공사"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        assert result["triage"]["action"] == ActionType.OUT_OF_SCOPE
        assert result["vector"]["results"] is not None
        assert result["output"]["answer"] is not None

    async def test_out_of_scope_operational_detail_routes_to_vector(self):
        """OUT_OF_SCOPE/operational_detail -> 식별 검색(VECTOR) 경로.

        회귀(사례 162-163): intake 가 신설 oos_type=operational_detail(폭염·휴무·주차·우천)
        을 산출하면 domain_outside 전면 거절로 새지 않고 식별 검색(vector)을 실제로 돌린다.
        검색 routing 은 attribute_gap 과 동일(is_gap_oos)하되 sub_intent 는
        "operational_detail" 전용으로 분리되어 answer 가 발췌 실답변/폴백을 고른다.
        (detail_excerpt 적재·발췌 답변 도달은 test_operational_detail_integration 이 커버.)
        """
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.OUT_OF_SCOPE,
            oos_type="operational_detail",
        )
        vrows = [
            {
                "service_id": "V001",
                "service_name": "마루공원 수영장",
                "similarity": 0.9,
            }
        ]
        hydrated = [
            {
                "service_id": "V001",
                "service_name": "마루공원 수영장",
                "service_url": "https://example.com",
            }
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            from agents.vector_agent import VectorAgent, _RefinedQuery

            vector_agent = VectorAgent.__new__(VectorAgent)
            refine_chain = MagicMock()
            refine_chain.ainvoke = AsyncMock(
                return_value=_RefinedQuery(
                    refined_query="마루공원 수영장",
                    max_class_name=None,
                    area_name=None,
                    service_status=None,
                )
            )
            vector_agent._refine_chain = refine_chain
            embeddings = MagicMock()
            embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
            vector_agent._embeddings = embeddings

            graph = AgentGraph(
                intake=intake,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("공식 페이지에서 확인하세요."),
            )
            result = await run_graph(
                graph,
                _state(message="마루공원 수영장 폭염철 이용안내"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        # 도메인 거절(domain_outside)로 새지 않아야 한다 — 식별 검색이 실제로 돌아간다.
        assert "out_of_scope_domain_outside" not in result["node_path"]
        assert "out_of_scope_operational_detail" in result["node_path"]
        assert result["vector"]["results"] is not None
        # 전용 신호: vector_sub_intent 가 operational_detail 로 세팅된다(answer 분기 신호).
        assert result["plan"].get("vector_sub_intent") == "operational_detail"
        assert result["output"]["answer"] is not None

    async def test_explain_with_prev_reasoning(self):
        """META turn_kind + prev_reasoning -> explain LLM 재서술 답변 생성."""
        intake = make_intake(
            turn_kind=TurnKind.META, user_rationale="판단 근거를 설명드립니다."
        )
        prev = "자연 체험 관련 키워드가 포함되어 있어 자연 체험으로 분류했습니다."

        graph = AgentGraph(
            intake=intake,
            answer_agent=_answer_agent("자연 체험으로 안내드린 이유를 설명드릴게요."),
        )
        result = await run_graph(
            graph,
            _state(message="왜 그렇게 판단했어?", prev_reasoning=prev),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["triage"]["turn_kind"] == "META"
        assert result["output"]["answer"] is not None
        # explain() 으로 재서술된 답변이 채워진다.
        assert len(result["output"]["answer"]) > 10

    # EXPLAIN + prev_reasoning 없음 → DIRECT_ANSWER 폴백은 test_non_retrieve_robustness
    # .TestExplainRephrase.test_explain_no_prev_reasoning_falls_back_to_direct_answer 가
    # 더 구체적으로 커버하는 중복이라 축소했다(EXPLAIN+prev 경로는 위에 유지).


# ---------------------------------------------------------------------------
# 2. self-correction 비-RETRIEVE 제외
# ---------------------------------------------------------------------------


class TestSelfCorrectionExcludesNonRetrieve:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer_agent())._nodes

    def test_direct_answer_no_retry(self):
        """DIRECT_ANSWER action은 빈 답변이어도 retry_prep 미진입."""
        nodes = self._nodes()
        state = _state(action=ActionType.DIRECT_ANSWER, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "end_normal"

    # AMBIGUOUS/OUT_OF_SCOPE 의 no-retry 도 동일 predicate(비-RETRIEVE → end_normal)의
    # 값만 다른 순열이라, 대표 DIRECT_ANSWER + EXPLAIN 만 남기고 축소했다.

    def test_explain_no_retry(self):
        """EXPLAIN action은 retry_prep 미진입."""
        nodes = self._nodes()
        state = _state(action=ActionType.EXPLAIN, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "end_normal"

    def test_retrieve_with_empty_answer_triggers_retry(self):
        """RETRIEVE action은 빈 답변 시 retry_prep 진입 (기존 동작 유지)."""
        nodes = self._nodes()
        state = _state(action=ActionType.RETRIEVE, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "retry_prep_node"

    def test_none_action_with_empty_answer_triggers_retry(self):
        """action=None(구버전 RouterAgent 경로)은 기존 동작 유지."""
        nodes = self._nodes()
        state = _state(action=None, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "retry_prep_node"


# ---------------------------------------------------------------------------
# 8. QA 회귀 — retry → router_node 재진입 (triage 미경유) + recursion_limit 경계
# ---------------------------------------------------------------------------


class TestRetryReentersRouterNotTriage:
    """책임 분리 핵심 불변식: self-correction 재시도는 router_node 로 재진입하고
    intake_node 는 재실행되지 않는다(turn_kind/action 은 이미 확정).

    검증 방법: intake/router LLM mock 의 with_structured_output().ainvoke 호출
    횟수를 센다. 0건 → retry_prep → 재진입의 E2E 사이클에서
      - intake LLM 호출 == 1 (재시도에도 재분류 없음)
      - router LLM 호출 == 2 (1차 + 재진입)
    node_path 에는 router 가 2회, intake 가 1회 누적된다.
    """

    async def test_retry_reenters_router_intake_runs_once(self):
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="검색합니다",
        )
        # router 는 VECTOR_SEARCH 를 반환 — _RETRY_FALLBACK_INTENT 전환 없이
        # 기존 완화로 router 재진입만 시키기 위해 VECTOR 경로를 쓴다.
        router = make_router(IntentType.VECTOR_SEARCH)
        answer_agent = _answer_agent("재시도 후 답변")

        intake_ainvoke = intake._llm.with_structured_output.return_value.ainvoke
        router_ainvoke = router._llm.with_structured_output.return_value.ainvoke

        with (
            patch("agents.hydration_node.hydrate_services", AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.VectorAgent.search",
                AsyncMock(return_value=[]),
            ),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=make_ai_session(),
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        # 재시도가 실제로 일어났는지 먼저 확인 (전제 보호)
        assert "retry_prep" in path, f"retry_prep 미발생: {path}"
        assert result["retry_count"] == 1

        # 핵심 불변식: intake 1회, router 2회.
        assert path.count("intake") == 1, f"intake 재실행됨: {path}"
        assert path.count("router") == 2, f"router 재진입 누락: {path}"
        assert intake_ainvoke.await_count == 1, "intake LLM 이 재시도에 재호출됨"
        assert router_ainvoke.await_count == 2, "router LLM 이 재진입에 미호출됨"

    async def test_retry_within_recursion_limit_completes(self):
        """recursion_limit=28 경계: retry 1회 포함 최악 RETRIEVE 경로가
        recursion 예외 없이 완주한다(off-by-one 회귀 가드).

        retry_prep → router → cache_check → search → hydration → rrf_fusion →
        pre_answer_gate → answer → cache_write → search_persist → trace 전 구간을
        다시 도는 사이클이 limit 안에서 종단되어야 한다.
        """
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="검색",
        )
        router = make_router(IntentType.VECTOR_SEARCH)
        answer_agent = _answer_agent("완주 답변")

        with (
            patch("agents.hydration_node.hydrate_services", AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.VectorAgent.search",
                AsyncMock(return_value=[]),
            ),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=make_ai_session(),
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        # recursion 예외가 나면 graph.run 의 except 핸들러가 trace 미경유로
        # fallback answer 를 주입한다. trace 도달은 정상 완주의 증거다.
        assert "trace" in path, f"종단 trace 미도달(recursion 한계 의심): {path}"
        assert result["retry_count"] == 1
        assert result["output"].get("answer")
