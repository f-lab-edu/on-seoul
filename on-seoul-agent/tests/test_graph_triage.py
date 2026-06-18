"""TriageAgent 통합 테스트 — 구현 검증.

검증 대상:
- 5 action 라우팅 각 경로
- RETRIEVE/secondary SQL+VECTOR fan-out -> RRF 융합 (enable_secondary_intent=True)
- RETRIEVE/단일: secondary=None 단일 라우트 (하위 호환)
- DIRECT_ANSWER: DB 미조회, LLM 직접 응답
- AMBIGUOUS: "좋은 곳" -> AMBIGUOUS
- OUT_OF_SCOPE/domain_outside: 사전 거절, 검색 미실행
- OUT_OF_SCOPE/attribute_gap: 엔티티 검색 -> service_url 포함
- EXPLAIN: prev_reasoning 있으면 근거 설명, 없으면 DIRECT_ANSWER 폴백
- 캐시 키: RETRIEVE 단일/멀티 충돌 없음, 비-RETRIEVE 캐시 제외
- self-correction: 비-RETRIEVE action이 0건 재시도 경로 미진입
- C2: 0건 시 answer LLM 미호출 + retry_prep 직행
"""

from unittest.mock import AsyncMock, MagicMock, patch


from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.triage_agent import TriageAgent
from core.cache import _cache_key
from schemas.state import ActionType, AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_router,
    make_sql_agent,
    make_triage,
    make_ai_session,
    patch_node_sessions,
    run_graph,
    stream_graph,
)


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _answer_agent(answer: str = "답변입니다."):
    return make_answer_agent(answer)


def _make_nodes(triage: TriageAgent) -> GraphNodes:
    return GraphNodes(
        triage=triage,
        answer_agent=_answer_agent(),
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
        triage = make_triage(ActionType.DIRECT_ANSWER, user_rationale="안녕하세요!")
        sql_agent, data_session = make_sql_agent([])

        graph = AgentGraph(
            triage=triage,
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
        triage = make_triage(
            ActionType.AMBIGUOUS,
            user_rationale="어떤 종류의 시설을 찾으시나요?",
        )
        graph = AgentGraph(triage=triage, answer_agent=_answer_agent())
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
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            user_rationale="서울 공공서비스 범위 밖입니다.",
        )
        sql_agent, data_session = make_sql_agent([])

        graph = AgentGraph(
            triage=triage, sql_agent=sql_agent, answer_agent=_answer_agent()
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
            "범위" in result["output"]["answer"] or "날씨" in result["output"]["answer"] or result["output"]["answer"]
        )
        sql_agent._chain.ainvoke.assert_not_called()

    async def test_out_of_scope_attribute_gap_triggers_vector(self):
        """OUT_OF_SCOPE/attribute_gap -> vector_node 경유 + 시설 안내."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            refined_query="마루공원 테니스장",
            vector_sub_intent="identification",
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
                triage=triage,
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

    async def test_explain_with_prev_reasoning(self):
        """EXPLAIN action + prev_reasoning -> LLM 재서술(S2) 답변 생성."""
        triage = make_triage(
            ActionType.EXPLAIN, user_rationale="판단 근거를 설명드립니다."
        )
        prev = "자연 체험 관련 키워드가 포함되어 있어 자연 체험으로 분류했습니다."

        graph = AgentGraph(
            triage=triage,
            answer_agent=_answer_agent("자연 체험으로 안내드린 이유를 설명드릴게요."),
        )
        result = await run_graph(
            graph,
            _state(message="왜 그렇게 판단했어?", prev_reasoning=prev),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["triage"]["action"] == ActionType.EXPLAIN
        assert result["output"]["answer"] is not None
        # S2: explain() 으로 재서술된 답변이 채워진다.
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
# 3. C2 pre-answer 0건 게이트
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
        """C2 게이트: 0건 시 answer_node LLM 미호출 + retry_prep 직행 E2E."""
        triage = make_triage(ActionType.RETRIEVE)
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])  # SQL 0건
        answer_agent = _answer_agent("재시도 후 답변")

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                triage=triage,
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
# 4. 캐시 키 충돌 없음 + 비-RETRIEVE 캐시 제외
# ---------------------------------------------------------------------------


class TestCacheKeyWithRoutes:
    def test_single_route_key(self):
        """단일 primary intent만 있을 때 캐시 키가 생성된다."""
        key1 = _cache_key("수영장", routes="SQL_SEARCH")
        key2 = _cache_key("수영장", routes="VECTOR_SEARCH")
        assert key1 != key2

    def test_multi_route_key_differs_from_single(self):
        """multi-route(SQL+VECTOR) 키가 단일 라우트 키와 다르다."""
        key_single = _cache_key("마포구 풋살장", routes="SQL_SEARCH")
        key_multi = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        assert key_single != key_multi

    def test_multi_route_key_order_independent(self):
        """routes 파라미터 내 순서 독립적으로 동일해야 한다."""
        # _cache_key는 routes 문자열을 그대로 사용하므로 CacheCheckNode에서 정렬하여 전달한다
        # 여기서는 정렬된 문자열이 같으면 동일한 키를 확인한다
        key1 = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        key2 = _cache_key("마포구 풋살장", routes="SQL_SEARCH,VECTOR_SEARCH")
        assert key1 == key2

    def test_non_retrieve_cache_excluded(self):
        """비-RETRIEVE action이면 CacheCheckNode가 cache_hit=False를 반환한다."""
        from agents.nodes import CacheCheckNode

        node = CacheCheckNode(redis=MagicMock())

        import asyncio

        async def _check(action: ActionType) -> bool:
            state = make_agent_state(
                action=action,
                intent=IntentType.SQL_SEARCH,
                refined_query="수영장",
            )
            result = await node(state)
            return result.get("cache_hit", False)

        for action in (
            ActionType.DIRECT_ANSWER,
            ActionType.AMBIGUOUS,
            ActionType.OUT_OF_SCOPE,
            ActionType.EXPLAIN,
        ):
            assert asyncio.get_event_loop().run_until_complete(_check(action)) is False

    def test_cache_write_excludes_non_retrieve(self):
        """비-RETRIEVE action이면 CacheWriteNode가 빈 dict를 반환한다."""
        from agents.nodes import CacheWriteNode

        node = CacheWriteNode(redis=MagicMock())

        import asyncio

        async def _write(action: ActionType) -> dict:
            state = make_agent_state(
                action=action,
                intent=IntentType.SQL_SEARCH,
                refined_query="수영장",
                answer="답변",
            )
            return await node(state)

        for action in (ActionType.DIRECT_ANSWER, ActionType.AMBIGUOUS):
            result = asyncio.get_event_loop().run_until_complete(_write(action))
            assert result == {}


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
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = False
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
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
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
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
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
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
        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
            result = await nodes.rrf_fusion_node(state)
        assert "rrf_fusion_empty" in result.get("node_path", [])


# ---------------------------------------------------------------------------
# 6. triage_node 슬롯 전파
# ---------------------------------------------------------------------------


class TestTriageNodeStatePropagation:
    async def test_triage_node_sets_action_only(self):
        """triage_node는 action/out_of_scope_type/user_rationale만 채운다(검색 계획 제외)."""
        triage = make_triage(
            ActionType.RETRIEVE,
            user_rationale="마포구 풋살장 검색",
        )
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.triage_node(_state(message="마포구 풋살장"))

        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["triage"]["user_rationale"] == "마포구 풋살장 검색"
        # 검색 계획은 router_node 책임 — triage_node update에 없어야 한다.
        assert "intent" not in update
        assert "secondary_intent" not in update
        assert "refined_query" not in update

    async def test_triage_node_does_not_honor_forced_intent(self):
        """forced_intent honor는 router_node로 이동했으므로 triage_node는 무시한다."""
        triage = make_triage(ActionType.RETRIEVE, user_rationale="검색")
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())

        structured = triage._llm.with_structured_output.return_value
        with patch_node_sessions():
            update = await nodes.triage_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )

        # triage_node는 forced_intent를 소비하지 않고 정상 LLM 분류를 수행한다.
        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert "intent" not in update
        structured.ainvoke.assert_called_once()

    async def test_triage_node_out_of_scope_slots(self):
        """triage_node: OUT_OF_SCOPE action이면 out_of_scope_type이 state에 채워진다."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            user_rationale="범위 밖입니다.",
        )
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.triage_node(_state())

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
            triage=make_triage(ActionType.RETRIEVE),
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
            triage=make_triage(ActionType.RETRIEVE),
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
            triage=make_triage(ActionType.RETRIEVE),
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


class TestRouterNodeRefineCache:
    """router_node refine 캐싱 (0-3-3) — LLM 호출 skip 검증."""

    def _nodes(self, router):
        return GraphNodes(
            triage=make_triage(ActionType.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
        )

    async def test_hit_skips_llm_and_restores_state(self):
        """refine 캐시 hit 시 LLM 미호출 + 저장값으로 update 복원."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        cached = {
            "intent": "VECTOR_SEARCH",
            "refined_query": "서울 테니스장",
            "max_class_name": "체육시설",
            "area_name": None,
            "service_status": None,
            "payment_type": None,
            "vector_sub_intent": "identification",
            "secondary_intent": None,
        }
        with (
            patch_node_sessions(),
            patch(
                "agents.nodes.get_cached_refine_by_key",
                AsyncMock(return_value=cached),
            ),
            patch("agents.nodes.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state(message="테니스장"))

        structured.ainvoke.assert_not_called()
        mock_set.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert update["plan"]["refined_query"] == "서울 테니스장"
        assert update["filters"]["max_class_name"] == "체육시설"
        assert update["plan"]["vector_sub_intent"] == "identification"
        assert "refine_cache_hit" in update["node_path"]

    async def test_miss_calls_llm_and_sets_cache(self):
        """refine 캐시 miss 시 LLM 호출 후 set 으로 채운다."""
        router = make_router(
            IntentType.SQL_SEARCH,
            refined_query="마포구 풋살장",
            max_class_name="체육시설",
        )
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        with (
            patch_node_sessions(),
            patch(
                "agents.nodes.get_cached_refine_by_key",
                AsyncMock(return_value=None),
            ),
            patch("agents.nodes.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state(message="마포구 풋살장"))

        structured.ainvoke.assert_called_once()
        mock_set.assert_called_once()
        # 저장값에 intent.value 직렬화 포함
        stored = mock_set.call_args.args[2]
        assert stored["intent"] == "SQL_SEARCH"
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_forced_intent_skips_cache(self):
        """forced_intent 경로는 refine 캐시를 조회/저장하지 않는다."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        with (
            patch_node_sessions(),
            patch("agents.nodes.get_cached_refine_by_key", AsyncMock()) as mock_get,
            patch("agents.nodes.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )
        mock_get.assert_not_called()
        mock_set.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH

    async def test_llm_error_does_not_set_cache(self):
        """classify 예외 시 캐시 SET 하지 않는다(에러 처리 유지)."""
        router = make_router(IntentType.SQL_SEARCH)
        nodes = self._nodes(router)
        structured = router._llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        with (
            patch_node_sessions(),
            patch(
                "agents.nodes.get_cached_refine_by_key",
                AsyncMock(return_value=None),
            ),
            patch("agents.nodes.set_cached_refine", AsyncMock()) as mock_set,
        ):
            update = await nodes.router_node(_state())
        mock_set.assert_not_called()
        assert "router_error" in update["node_path"]

    def test_serialize_restore_roundtrip_secondary_intent(self):
        """serialize→restore 대칭: secondary_intent(IntentType) round-trip 보존.

        _serialize_refine 은 IntentType→.value(str), _restore_refine 은 .value→IntentType
        로 복원한다. 캐시 SET 시 저장된 secondary_intent 가 HIT 복원 시 동일 enum 으로
        돌아오는지(데이터 무손실) 검증한다. 직접 round-trip 으로 secondary 분기를 단언.
        """
        from agents.nodes import _restore_refine, _serialize_refine

        update = {
            "plan": {
                "intent": IntentType.SQL_SEARCH,
                "refined_query": "마포구 풋살장",
                "secondary_intent": IntentType.VECTOR_SEARCH,
            },
            "filters": {"max_class_name": "체육시설"},
        }
        stored = _serialize_refine(update)
        # JSON 직렬화 가능한 str 로 저장
        assert stored["intent"] == "SQL_SEARCH"
        assert stored["secondary_intent"] == "VECTOR_SEARCH"

        restored = _restore_refine(stored)
        assert restored["plan"]["intent"] is IntentType.SQL_SEARCH
        assert restored["plan"]["secondary_intent"] is IntentType.VECTOR_SEARCH
        assert restored["plan"]["refined_query"] == "마포구 풋살장"
        assert restored["filters"]["max_class_name"] == "체육시설"

    def test_serialize_restore_omits_none_fields(self):
        """None 필드는 직렬화/복원 모두에서 생략(retry 경로 초기화 보존, 대칭)."""
        from agents.nodes import _restore_refine, _serialize_refine

        update = {"plan": {"intent": IntentType.VECTOR_SEARCH}}  # 선택 필드 전부 미존재
        stored = _serialize_refine(update)
        assert "refined_query" not in stored
        assert "secondary_intent" not in stored

        restored = _restore_refine(stored)
        assert restored == {"plan": {"intent": IntentType.VECTOR_SEARCH}}

    def test_restore_skips_explicit_none_values(self):
        """캐시 dict 에 명시적 None 이 있어도 update 에 키를 넣지 않는다(line 215-217)."""
        from agents.nodes import _restore_refine

        cached = {
            "intent": "SQL_SEARCH",
            "refined_query": None,
            "area_name": None,
            "secondary_intent": None,
        }
        restored = _restore_refine(cached)
        assert restored == {"plan": {"intent": IntentType.SQL_SEARCH}}


class TestRouterNodeRefineSingleflight:
    """router_node refine hop singleflight — answer singleflight 대칭.

    동시 cold-miss 시 첫 호출자만 classify, 나머지는 poll 로 refine_cache hit.
    락 누수 금지(try/finally), forced_intent 경로 미진입, fail-open 검증.
    """

    def _nodes(self, router, redis):
        return GraphNodes(
            triage=make_triage(ActionType.RETRIEVE),
            router=router,
            answer_agent=_answer_agent(),
            redis=redis,
        )

    async def test_waiter_polls_and_skips_classify(self):
        """락 미획득 호출자는 poll 로 refine hit → classify 호출 0."""
        import json

        router = make_router(IntentType.SQL_SEARCH)
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        # GET(by_key) miss → acquire 실패(다른 보유자) → poll 에서 hit.
        cached = {"intent": "VECTOR_SEARCH", "refined_query": "서울 테니스장"}
        redis.get.side_effect = [None, json.dumps(cached)]
        redis.set.return_value = None  # SET NX 실패 = 락 미획득
        nodes = self._nodes(router, redis)
        with patch_node_sessions(), patch("asyncio.sleep", AsyncMock()):
            update = await nodes.router_node(_state(message="테니스장"))

        structured.ainvoke.assert_not_called()  # classify 중복 0
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH
        assert "refine_cache_hit" in update["node_path"]

    async def test_holder_acquires_classifies_and_releases(self):
        """락 보유자는 classify 실행 후 release_refine_lock(DEL) 호출."""
        from core.cache import _LOCK_SUFFIX

        router = make_router(IntentType.SQL_SEARCH, refined_query="마포구 풋살장")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None  # GET miss
        redis.set.return_value = True  # SET NX 성공 = 락 보유
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state(message="마포구 풋살장"))

        structured.ainvoke.assert_called_once()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH
        # release 가 락 키(캐시 키 + :lock)로 DEL 됨.
        delete_keys = [c.args[0] for c in redis.delete.call_args_list]
        assert any(k.endswith(_LOCK_SUFFIX) for k in delete_keys)

    async def test_lock_released_on_classify_exception(self):
        """★ 락 누수 가드: classify 예외에도 finally 가 release_refine_lock 호출."""
        from core.cache import _LOCK_SUFFIX

        router = make_router(IntentType.SQL_SEARCH)
        structured = router._llm.with_structured_output.return_value
        structured.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        redis = AsyncMock()
        redis.get.return_value = None
        redis.set.return_value = True  # 락 보유
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state())

        assert "router_error" in update["node_path"]
        # 예외 경로에서도 락 해제됨.
        delete_keys = [c.args[0] for c in redis.delete.call_args_list]
        assert any(k.endswith(_LOCK_SUFFIX) for k in delete_keys)

    async def test_forced_intent_skips_lock(self):
        """forced_intent 경로는 refine 락을 acquire/release/poll 하지 않는다."""
        router = make_router(IntentType.SQL_SEARCH)
        redis = AsyncMock()
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )
        # GET/SET NX/DEL 어느 것도 호출되지 않음(refine 경로 미진입).
        redis.set.assert_not_called()
        redis.delete.assert_not_called()
        redis.get.assert_not_called()
        assert update["plan"]["intent"] == IntentType.VECTOR_SEARCH

    async def test_fail_open_on_poll_timeout_runs_classify(self):
        """락 미획득 + poll 타임아웃 → fail-open: classify 실행, 락 미해제."""
        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None  # GET miss + poll 매회 None
        redis.set.return_value = None  # 락 미획득(waiter)
        nodes = self._nodes(router, redis)
        with patch_node_sessions(), patch("asyncio.sleep", AsyncMock()):
            update = await nodes.router_node(_state(message="q"))

        structured.ainvoke.assert_called_once()  # fail-open 으로 직접 classify
        # 락 미보유라 release(DEL) 하지 않음.
        redis.delete.assert_not_called()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_fail_open_on_acquire_redis_error_runs_classify(self):
        """Redis acquire 예외 → fail-open True → classify 실행."""
        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        structured = router._llm.with_structured_output.return_value
        redis = AsyncMock()
        redis.get.return_value = None
        redis.set.side_effect = RuntimeError("redis down")  # acquire 예외 → fail-open
        nodes = self._nodes(router, redis)
        with patch_node_sessions():
            update = await nodes.router_node(_state(message="q"))

        structured.ainvoke.assert_called_once()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH

    async def test_disabled_toggle_skips_lock(self):
        """refine singleflight 비활성화 → acquire no-op(True), SET NX·DEL 미호출.

        cache write(set_cached_refine)는 SET EX 로 여전히 호출되므로, 락 전용
        SET NX(nx=True) 와 release(DEL) 만 미호출인지 구분해 단언한다.
        """
        from core.config import settings as cfg

        router = make_router(IntentType.SQL_SEARCH, refined_query="q")
        redis = AsyncMock()
        redis.get.return_value = None
        nodes = self._nodes(router, redis)
        with (
            patch_node_sessions(),
            patch.object(cfg, "refine_cache_singleflight_enabled", False),
        ):
            update = await nodes.router_node(_state(message="q"))
        # 락 게이트 off → SET NX(acquire) 미호출(cache SET EX 는 허용)·DEL(release) 미호출.
        nx_sets = [c for c in redis.set.call_args_list if c.kwargs.get("nx")]
        assert nx_sets == []
        redis.delete.assert_not_called()
        assert update["plan"]["intent"] == IntentType.SQL_SEARCH


# ---------------------------------------------------------------------------
# 7. stream() 이벤트 - action 경로
# ---------------------------------------------------------------------------


class TestStreamEventsWithTriage:
    async def _collect(self, gen):
        events = []
        async for event_type, data in gen:
            events.append((event_type, data))
        return events

    async def test_direct_answer_emits_answering_not_searching(self):
        """DIRECT_ANSWER action은 searching progress 없이 answering 바로 방출."""
        triage = make_triage(ActionType.DIRECT_ANSWER)
        graph = AgentGraph(triage=triage, answer_agent=_answer_agent("직접 답변"))

        events = await self._collect(
            stream_graph(
                graph,
                _state(message="안녕하세요"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )
        )
        steps = [d["step"] for t, d in events if t == "progress"]
        assert "answering" in steps
        assert "searching" not in steps

    # RETRIEVE/SQL searching present 는 test_graph 의 progress 순서 테스트
    # (fanout/router-only)가 이미 커버하므로 축소했다. DIRECT_ANSWER 의 searching
    # 미방출(고유 negative 분기)은 위에 유지한다.


# ---------------------------------------------------------------------------
# 8. QA 회귀 — retry → router_node 재진입 (triage 미경유) + recursion_limit 경계
# ---------------------------------------------------------------------------


class TestRetryReentersRouterNotTriage:
    """책임 분리 핵심 불변식: self-correction 재시도는 router_node 로 재진입하고
    triage_node 는 재실행되지 않는다(action 은 이미 RETRIEVE 로 확정).

    검증 방법: triage/router LLM mock 의 with_structured_output().ainvoke 호출
    횟수를 센다. 0건 → retry_prep → 재진입의 E2E 사이클에서
      - triage LLM 호출 == 1 (재시도에도 재분류 없음)
      - router LLM 호출 == 2 (1차 + 재진입)
    node_path 에는 router 가 2회, triage 가 1회 누적된다.
    """

    async def test_retry_reenters_router_triage_runs_once(self):
        triage = make_triage(ActionType.RETRIEVE, user_rationale="검색합니다")
        # router 는 VECTOR_SEARCH 를 반환 — _RETRY_FALLBACK_INTENT 전환 없이
        # 케이스 C(완화)로 router 재진입만 시키기 위해 VECTOR 경로를 쓴다.
        router = make_router(IntentType.VECTOR_SEARCH)
        answer_agent = _answer_agent("재시도 후 답변")

        triage_ainvoke = triage._llm.with_structured_output.return_value.ainvoke
        router_ainvoke = router._llm.with_structured_output.return_value.ainvoke

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ), patch(
            "agents.vector_agent.VectorAgent.search",
            AsyncMock(return_value=[]),
        ):
            graph = AgentGraph(
                triage=triage,
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

        # 핵심 불변식: triage 1회, router 2회.
        assert path.count("triage") == 1, f"triage 재실행됨: {path}"
        assert path.count("router") == 2, f"router 재진입 누락: {path}"
        assert triage_ainvoke.await_count == 1, "triage LLM 이 재시도에 재호출됨"
        assert router_ainvoke.await_count == 2, "router LLM 이 재진입에 미호출됨"

    async def test_retry_within_recursion_limit_completes(self):
        """recursion_limit=28 경계: retry 1회 포함 최악 RETRIEVE 경로가
        recursion 예외 없이 완주한다(off-by-one 회귀 가드).

        retry_prep → router → cache_check → search → hydration → rrf_fusion →
        pre_answer_gate → answer → cache_write → search_persist → trace 전 구간을
        다시 도는 사이클이 limit 안에서 종단되어야 한다.
        """
        triage = make_triage(ActionType.RETRIEVE, user_rationale="검색")
        router = make_router(IntentType.VECTOR_SEARCH)
        answer_agent = _answer_agent("완주 답변")

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ), patch(
            "agents.vector_agent.VectorAgent.search",
            AsyncMock(return_value=[]),
        ):
            graph = AgentGraph(
                triage=triage,
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
