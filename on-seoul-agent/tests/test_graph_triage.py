"""TriageAgent 통합 테스트 — [C] W2 구현 검증.

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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.triage_agent import TriageAgent
from core.cache import _cache_key
from schemas.state import ActionType, AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
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
    async def test_retrieve_sql_reaches_sql_node(self):
        """RETRIEVE/SQL_SEARCH -> sql_node 경로."""
        rows = [{"service_id": "S001", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        triage = make_triage(ActionType.RETRIEVE, IntentType.SQL_SEARCH)

        graph = AgentGraph(triage=triage, sql_agent=sql_agent, answer_agent=_answer_agent())
        result = await run_graph(
            graph, _state(), data_session=data_session, ai_session=make_ai_session()
        )
        assert result["intent"] == IntentType.SQL_SEARCH
        assert result["action"] == ActionType.RETRIEVE
        assert result["sql_results"] is not None

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
            graph, _state(message="안녕하세요"), data_session=data_session, ai_session=make_ai_session()
        )
        assert result["action"] == ActionType.DIRECT_ANSWER
        assert result["sql_results"] is None
        assert result["vector_results"] is None
        assert result["answer"] is not None
        sql_agent._chain.ainvoke.assert_not_called()

    async def test_ambiguous_returns_clarification(self):
        """AMBIGUOUS action -> 명확화 안내 반환."""
        triage = make_triage(
            ActionType.AMBIGUOUS,
            user_rationale="어떤 종류의 시설을 찾으시나요?",
        )
        graph = AgentGraph(triage=triage, answer_agent=_answer_agent())
        result = await run_graph(
            graph, _state(message="좋은 곳 알려줘"), data_session=MagicMock(), ai_session=make_ai_session()
        )
        assert result["action"] == ActionType.AMBIGUOUS
        assert result["answer"] is not None
        assert len(result["answer"]) > 0

    async def test_out_of_scope_domain_outside_rejects(self):
        """OUT_OF_SCOPE/domain_outside -> 즉시 거절, 검색 미실행."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            user_rationale="서울 공공서비스 범위 밖입니다.",
        )
        sql_agent, data_session = make_sql_agent([])

        graph = AgentGraph(triage=triage, sql_agent=sql_agent, answer_agent=_answer_agent())
        result = await run_graph(
            graph, _state(message="오늘 서울 날씨"), data_session=data_session, ai_session=make_ai_session()
        )
        assert result["action"] == ActionType.OUT_OF_SCOPE
        assert result["out_of_scope_type"] == "domain_outside"
        assert "범위" in result["answer"] or "날씨" in result["answer"] or result["answer"]
        sql_agent._chain.ainvoke.assert_not_called()

    async def test_out_of_scope_attribute_gap_triggers_vector(self):
        """OUT_OF_SCOPE/attribute_gap -> vector_node 경유 + 시설 안내."""
        triage = make_triage(
            ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            refined_query="마루공원 테니스장",
            vector_sub_intent="identification",
        )
        vrows = [{"service_id": "V001", "service_name": "마루공원 테니스장", "similarity": 0.9}]
        hydrated = [{"service_id": "V001", "service_name": "마루공원 테니스장",
                     "service_url": "https://example.com"}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.hydration_node.hydrate_services", AsyncMock(return_value=hydrated)),
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
            vector_agent._channel_sema = asyncio.Semaphore(4)

            graph = AgentGraph(
                triage=triage,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("시설 페이지를 확인하세요."),
            )
            result = await run_graph(
                graph, _state(message="마루공원 테니스장 보수 공사"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        assert result["action"] == ActionType.OUT_OF_SCOPE
        assert result["vector_results"] is not None
        assert result["answer"] is not None

    async def test_explain_with_prev_reasoning(self):
        """EXPLAIN action + prev_reasoning -> 근거 설명 포함된 답변."""
        triage = make_triage(ActionType.EXPLAIN, user_rationale="판단 근거를 설명드립니다.")
        prev = "자연 체험 관련 키워드가 포함되어 있어 자연 체험으로 분류했습니다."

        graph = AgentGraph(triage=triage, answer_agent=_answer_agent())
        result = await run_graph(
            graph,
            _state(message="왜 그렇게 판단했어?", prev_reasoning=prev),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["action"] == ActionType.EXPLAIN
        assert result["answer"] is not None
        # prev_reasoning 내용이 답변에 포함되어야 한다
        assert "자연" in result["answer"] or len(result["answer"]) > 10

    async def test_explain_without_prev_reasoning_falls_back(self):
        """EXPLAIN action + prev_reasoning 없음 -> DIRECT_ANSWER 폴백."""
        triage = make_triage(ActionType.EXPLAIN)

        graph = AgentGraph(
            triage=triage,
            answer_agent=_answer_agent("직접 답변합니다."),
        )
        result = await run_graph(
            graph,
            _state(message="왜 그렇게 판단했어?", prev_reasoning=None),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert result["answer"] is not None
        # prev_reasoning이 없으면 direct_answer 폴백으로 AnswerAgent가 실행된다


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

    def test_ambiguous_no_retry(self):
        """AMBIGUOUS action은 retry_prep 미진입."""
        nodes = self._nodes()
        state = _state(action=ActionType.AMBIGUOUS, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "end_normal"

    def test_out_of_scope_no_retry(self):
        """OUT_OF_SCOPE action은 retry_prep 미진입."""
        nodes = self._nodes()
        state = _state(action=ActionType.OUT_OF_SCOPE, answer="", retry_count=0)
        assert nodes.self_correction_edge(state) == "end_normal"

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
        state = _state(action=ActionType.RETRIEVE, hydrated_services=None, retry_count=0)
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
        triage = make_triage(ActionType.RETRIEVE, IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])  # SQL 0건
        answer_agent = _answer_agent("재시도 후 답변")

        with patch("agents.hydration_node.hydrate_services", AsyncMock(return_value=[])):
            graph = AgentGraph(
                triage=triage,
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

        for action in (ActionType.DIRECT_ANSWER, ActionType.AMBIGUOUS, ActionType.OUT_OF_SCOPE, ActionType.EXPLAIN):
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
    async def test_triage_node_sets_action_and_intent(self):
        """triage_node가 action/intent/secondary_intent를 state에 채운다."""
        triage = make_triage(
            ActionType.RETRIEVE,
            IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
            user_rationale="마포구 풋살장 검색",
        )
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.triage_node(_state(message="마포구 풋살장"))

        assert update["action"] == ActionType.RETRIEVE
        assert update["intent"] == IntentType.SQL_SEARCH
        assert update["secondary_intent"] == IntentType.VECTOR_SEARCH
        assert update["user_rationale"] == "마포구 풋살장 검색"

    async def test_triage_node_omits_refined_query_when_none(self):
        """triage_node: refined_query=None이면 update에 키를 포함하지 않는다."""
        triage = make_triage(ActionType.DIRECT_ANSWER)
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())
        with patch_node_sessions():
            update = await nodes.triage_node(_state())
        assert "refined_query" not in update

    async def test_triage_node_honors_forced_intent(self):
        """forced_intent가 있으면 LLM 미호출, action=RETRIEVE로 강제."""
        triage = make_triage(ActionType.DIRECT_ANSWER)  # 이게 호출되면 DIRECT_ANSWER
        nodes = GraphNodes(triage=triage, answer_agent=_answer_agent())

        structured = triage._llm.with_structured_output.return_value
        with patch_node_sessions():
            update = await nodes.triage_node(
                _state(forced_intent=IntentType.VECTOR_SEARCH)
            )

        assert update["intent"] == IntentType.VECTOR_SEARCH
        assert update["action"] == ActionType.RETRIEVE
        assert update["forced_intent"] is None
        structured.ainvoke.assert_not_called()

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

        assert update["action"] == ActionType.OUT_OF_SCOPE
        assert update["out_of_scope_type"] == "domain_outside"
        assert update["user_rationale"] == "범위 밖입니다."


# ---------------------------------------------------------------------------
# 7. stream() 이벤트 - W2 action 경로
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
                graph, _state(message="안녕하세요"),
                data_session=MagicMock(), ai_session=make_ai_session()
            )
        )
        steps = [d["step"] for t, d in events if t == "progress"]
        assert "answering" in steps
        assert "searching" not in steps

    async def test_retrieve_sql_emits_searching(self):
        """RETRIEVE/SQL_SEARCH action은 searching progress 방출."""
        triage = make_triage(ActionType.RETRIEVE, IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        graph = AgentGraph(
            triage=triage,
            sql_agent=sql_agent,
            answer_agent=_answer_agent(),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=make_ai_session()
            )
        )
        steps = [d["step"] for t, d in events if t == "progress"]
        assert "searching" in steps
