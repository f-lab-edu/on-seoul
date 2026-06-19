"""non-RETRIEVE action 자가교정 + 품질 안전망 (M1 / S1 / S2).

- M1: attribute_gap 0건 → retry_prep attribute_gap 분기(검색 컨텍스트 보존) →
      필터 완화 재검색. forced_intent=VECTOR_SEARCH + refined_query/vector_sub_intent
      보존. relaxed_filters 기록. 완화 후 결과 有/無 3-상태. 무한루프 없음.
- S1: direct_answer_node / ambiguous_node 빈 답변 가드(노드별).
- S2: AnswerAgent.explain() LLM 재서술 — prev_reasoning 기술 토큰 비노출,
      prev_reasoning 없음→direct_answer 폴백, LLM 예외→폴백.

모든 LLM/외부 호출은 fake 로 차단한다(hermetic).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent, _STRUCT_CARD_LIST, _STRUCT_FALLBACK
from agents.graph import AgentGraph
from agents.nodes import _FALLBACK_ANSWER, GraphNodes
from schemas.state import ActionType, AgentState, IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_ai_session,
    make_triage,
    run_graph,
)


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _attribute_gap_triage():
    return make_triage(
        ActionType.OUT_OF_SCOPE,
        out_of_scope_type="attribute_gap",
        user_rationale="특정 시설 식별이 필요합니다.",
    )


# ---------------------------------------------------------------------------
# M1-a — route_pre_answer_gate: attribute_gap 0건도 0건 체크 경로
# ---------------------------------------------------------------------------


class TestPreAnswerGateAttributeGap:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=make_answer_agent())._nodes

    def test_attribute_gap_zero_hits_routes_retry_prep(self):
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            hydrated_services=[],
            retry_count=0,
        )
        assert nodes.route_pre_answer_gate(state) == "retry_prep_node"

    def test_attribute_gap_with_results_routes_answer(self):
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_attribute_gap_zero_hits_capped_after_retry(self):
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            hydrated_services=[],
            retry_count=1,
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_domain_outside_passes_through(self):
        """domain_outside(검색 경로 아님)는 게이트 통과."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="domain_outside",
            hydrated_services=[],
            retry_count=0,
        )
        assert nodes.route_pre_answer_gate(state) == "answer_node"


# ---------------------------------------------------------------------------
# M1-재진입 — retry_prep attribute_gap 분기(검색 컨텍스트 보존)
# ---------------------------------------------------------------------------


class TestRetryPrepAttributeGapBranch:
    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=make_answer_agent())._nodes

    async def test_preserves_context_and_relaxes_filters(self):
        """attribute_gap 0건 재시도: forced_intent=VECTOR_SEARCH + refined_query/
        vector_sub_intent 보존 + 0건 유발 필터만 드롭 + relaxed_filters 기록."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="identification",
            refined_query="마루공원 테니스장",
            payment_type="무료",
            area_name="강남구",
            max_class_name="체육시설",
            retry_count=0,
        )
        with patch("agents.nodes.release_answer_lock", AsyncMock()):
            update = await nodes.retry_prep_node(state)

        # forced_intent 로 2회차 router_node 가 재분류 skip.
        assert update["forced_intent"] == IntentType.VECTOR_SEARCH
        # 검색 컨텍스트 보존: plan 리셋 금지(refined_query/vector_sub_intent 미포함).
        plan_update = update.get("plan", {})
        assert "refined_query" not in plan_update
        assert "vector_sub_intent" not in plan_update
        # 공통 베이스.
        assert update["retry_relaxed"] is True
        assert update["retry_count"] == 1
        # 0건 유발 필터 드롭 + relaxed_filters 기록. max_class_name 유지.
        assert update["filters"] == {"payment_type": None, "area_name": None}
        assert set(update["relaxed_filters"]) == {"payment_type", "area_name"}
        assert "max_class_name" not in update["filters"]

    async def test_no_filters_relaxed_filters_empty(self):
        """드롭할 필터가 없으면 relaxed_filters=[] (max_class_name 만 있을 때)."""
        nodes = self._nodes()
        state = _state(
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="identification",
            refined_query="마루공원 테니스장",
            max_class_name="체육시설",
            retry_count=0,
        )
        with patch("agents.nodes.release_answer_lock", AsyncMock()):
            update = await nodes.retry_prep_node(state)
        assert update["relaxed_filters"] == []
        assert update["forced_intent"] == IntentType.VECTOR_SEARCH


# ---------------------------------------------------------------------------
# M1 E2E — 3-상태 표
# ---------------------------------------------------------------------------


class TestAttributeGapRelaxRetryE2E:
    def _vector_search_side_effect(self, pass_results: list[list[dict]]):
        """pass 별로 다른 vector 결과를 반환하는 VectorAgent.search side_effect.

        VectorAgent.search 가 내부에서 채널별로 vector_search 를 여러 번 부르므로,
        채널 호출 카운트가 아니라 search() 자체를 패치해 "pass 단위"로 제어한다.
        """
        calls = {"n": 0}

        async def _search(state, *args, **kwargs):
            idx = min(calls["n"], len(pass_results) - 1)
            rows = pass_results[idx]
            calls["n"] += 1
            return {
                "plan": {"refined_query": "마루공원 테니스장"},
                "vector": {"results": rows},
            }

        _search.calls = calls
        return _search

    async def test_relaxed_hit_sets_retry_relaxed_and_notice(self):
        """완화 후 결과 有: retry_relaxed=True & relaxed_filters 채워짐 &
        '대신 이런 곳' 톤 완화 안내가 answer system 에 실린다."""
        triage = _attribute_gap_triage()
        # 1차 vector 0건, 2차(완화 후) vector 1건.
        vrows_hit = [
            {"service_id": "V1", "service_name": "마루공원 테니스장", "similarity": 0.9}
        ]
        hydrated_hit = [
            {
                "service_id": "V1",
                "service_name": "마루공원 테니스장",
                "service_url": "https://example.com",
                "payment_type": "유료",
            }
        ]

        async def _hydrate(session, ids):
            return hydrated_hit if ids else []

        # 실제 AnswerAgent + fake LLM 으로 완화 안내 절 노출을 직접 단언.
        mock_model = MagicMock()
        mock_model.__or__ = MagicMock(return_value=MagicMock())
        mock_model.with_structured_output = MagicMock(return_value=MagicMock())
        answer_agent = AnswerAgent(model=mock_model)
        answer_agent._answer_chain = MagicMock()
        answer_agent._answer_chain.ainvoke = AsyncMock(
            return_value="말씀하신 곳은 못 찾았지만 대신 이런 곳은 어떠세요?"
        )

        side = self._vector_search_side_effect([[], vrows_hit])

        from tests.helpers import make_router

        router = make_router(IntentType.VECTOR_SEARCH, payment_type="무료")
        with patch(
            "agents.vector_agent.VectorAgent.search", AsyncMock(side_effect=side)
        ), patch(
            "agents.hydration_node.hydrate_services", AsyncMock(side_effect=_hydrate)
        ):
            graph = AgentGraph(
                triage=triage,
                router=router,
                answer_agent=answer_agent,
            )
            # attribute_gap 경로는 router_node 를 거치지 않으므로(out_of_scope_node →
            # vector_node) 필터는 triage/refine 단계에서 해소된 것으로 보고 초기 state 에
            # 둔다. retry_prep attribute_gap 분기가 이 필터를 드롭·relaxed_filters 기록한다.
            result = await run_graph(
                graph,
                _state(
                    message="마루공원 테니스장 무료로 빌릴 수 있어?",
                    payment_type="무료",
                ),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        assert result["retry_count"] == 1
        assert result["retry_relaxed"] is True
        assert result["relaxed_filters"]  # 비어있지 않음
        # 완화 안내 절이 answer system 에 실렸는지(마지막 answer 호출).
        last_system = answer_agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert "완화한 결과입니다" in last_system
        # 유료→무료 오안내 가드 보존.
        assert "유료 시설을 무료라고 표현하지 마세요" in last_system

    async def test_relaxed_still_zero_honest_message(self):
        """완화 후에도 0건: retry_count 캡으로 answer_node 통과 → '찾지 못했습니다' 정직 안내."""
        triage = _attribute_gap_triage()
        answer_agent = make_answer_agent("죄송합니다, 조건에 맞는 시설을 찾지 못했습니다.")
        side = self._vector_search_side_effect([[], []])

        from tests.helpers import make_router

        router = make_router(IntentType.VECTOR_SEARCH, payment_type="무료")
        with patch(
            "agents.vector_agent.VectorAgent.search", AsyncMock(side_effect=side)
        ), patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                triage=triage,
                router=router,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(message="없는시설 무료로 빌릴 수 있어?"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        assert result["retry_count"] == 1
        assert "찾지 못했습니다" in result["output"]["answer"]

    async def test_no_infinite_loop_terminates(self):
        """무한루프 없음: attribute_gap 완화 재시도가 2회차에 종단(trace 도달)."""
        triage = _attribute_gap_triage()
        answer_agent = make_answer_agent("정직 안내")
        side = self._vector_search_side_effect([[], []])

        from tests.helpers import make_router

        router = make_router(IntentType.VECTOR_SEARCH, payment_type="무료")
        with patch(
            "agents.vector_agent.VectorAgent.search", AsyncMock(side_effect=side)
        ), patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                triage=triage,
                router=router,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(message="없는시설"),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        assert "trace" in path, f"종단 trace 미도달(무한루프 의심): {path}"
        assert result["retry_count"] == 1
        # retry_prep 가 정확히 1회만 일어남.
        assert path.count("retry_prep") == 1


# ---------------------------------------------------------------------------
# M1-b — relaxed_filters 라벨 매핑 + 유료→무료 오안내 회귀
# ---------------------------------------------------------------------------


class TestRelaxedFilterLabels:
    def test_labels_match_dropped_filters(self):
        from agents.answer_agent import _relaxed_notice

        notice = _relaxed_notice(["payment_type", "area_name"])
        assert "요금 조건" in notice
        assert "지역" in notice
        # 드롭하지 않은 필터 라벨은 없음.
        assert "카테고리" not in notice
        assert "접수 상태" not in notice

    def test_empty_falls_back_to_generic_notice(self):
        from agents.answer_agent import _relaxed_notice

        notice = _relaxed_notice([])
        assert "조건을 완화한 결과입니다" in notice
        # 특정 라벨을 임의로 넣지 않는다.
        assert "요금 조건" not in notice

    def test_paid_not_misreported_as_free_guard_preserved(self):
        from agents.answer_agent import _relaxed_notice

        for filters in ([], ["payment_type"], ["area_name", "service_status"]):
            assert "유료 시설을 무료라고 표현하지 마세요" in _relaxed_notice(filters)

    def test_unknown_filter_ignored(self):
        """매핑에 없는 키는 라벨로 노출하지 않는다(KeyError 회피)."""
        from agents.answer_agent import _relaxed_notice

        notice = _relaxed_notice(["nonexistent_key", "area_name"])
        assert "지역" in notice
        assert "nonexistent_key" not in notice


# ---------------------------------------------------------------------------
# S1 — 빈 답변 가드 (노드별)
# ---------------------------------------------------------------------------


class TestEmptyAnswerGuard:
    async def test_direct_answer_empty_uses_fallback(self):
        """AnswerAgent 가 빈 answer 반환 시 direct_answer_node 가 폴백 문구 세팅."""
        agent = make_answer_agent("")  # 빈 답변
        nodes = GraphNodes(
            triage=make_triage(ActionType.DIRECT_ANSWER), answer_agent=agent
        )
        update = await nodes.direct_answer_node(_state(message="안녕", intent=None))
        assert update["output"]["answer"] == _FALLBACK_ANSWER
        assert update["node_path"] == ["direct_answer_node"]

    async def test_direct_answer_whitespace_uses_fallback(self):
        agent = make_answer_agent("   \n  ")
        nodes = GraphNodes(
            triage=make_triage(ActionType.DIRECT_ANSWER), answer_agent=agent
        )
        update = await nodes.direct_answer_node(_state(message="안녕", intent=None))
        assert update["output"]["answer"] == _FALLBACK_ANSWER

    async def test_direct_answer_nonempty_passes_through(self):
        agent = make_answer_agent("안녕하세요! 무엇을 도와드릴까요?")
        nodes = GraphNodes(
            triage=make_triage(ActionType.DIRECT_ANSWER), answer_agent=agent
        )
        update = await nodes.direct_answer_node(_state(message="안녕", intent=None))
        assert update["output"]["answer"] == "안녕하세요! 무엇을 도와드릴까요?"

    async def test_ambiguous_empty_uses_clarify_fallback(self):
        from agents.answer_agent import _CLARIFY_FALLBACK

        agent = make_answer_agent()
        # clarify() 가 빈 answer 를 반환하도록 직접 mock.
        agent.clarify = AsyncMock(
            return_value={**_state(message="좋은 곳"), "answer": "", "service_cards": []}
        )
        nodes = GraphNodes(
            triage=make_triage(ActionType.AMBIGUOUS), answer_agent=agent
        )
        update = await nodes.ambiguous_node(_state(message="좋은 곳"))
        assert update["output"]["answer"] == _CLARIFY_FALLBACK
        assert update["node_path"] == ["ambiguous_node"]


# ---------------------------------------------------------------------------
# S2 — EXPLAIN LLM 재서술
# ---------------------------------------------------------------------------


class TestExplainRephrase:
    def _real_answer_agent(self, return_text: str) -> AnswerAgent:
        mock_model = MagicMock()
        mock_model.__or__ = MagicMock(return_value=MagicMock())
        mock_model.with_structured_output = MagicMock(return_value=MagicMock())
        agent = AnswerAgent(model=mock_model)
        agent._answer_chain = MagicMock()
        agent._answer_chain.ainvoke = AsyncMock(return_value=return_text)
        return agent

    async def test_explain_input_minimized_no_raw_tokens_in_prompt(self):
        """prev_reasoning 에 기술 토큰이 있어도 EXPLAIN 프롬프트가 비노출을 강제한다.

        explain() 이 EXPLAIN system 프롬프트를 고르고, 그 프롬프트가 기술 토큰
        비노출 지시를 담고 있는지(=출력에 raw 토큰이 새지 않도록 강제) 단언한다.
        """
        agent = self._real_answer_agent("자연 체험으로 안내드린 이유를 쉽게 설명드릴게요.")
        nodes = GraphNodes(
            triage=make_triage(ActionType.EXPLAIN), answer_agent=agent
        )
        prev = "intent=VECTOR_SEARCH, area_name=강남구, service_id=S001 로 분류함"
        update = await nodes.explain_node(
            _state(message="왜 그랬어?", prev_reasoning=prev)
        )

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        system = call_kwargs["system"]
        # EXPLAIN 프롬프트 선택 — FALLBACK/카드 프롬프트 아님.
        assert _STRUCT_FALLBACK[:30] not in system
        assert _STRUCT_CARD_LIST[:30] not in system
        # 기술 토큰 비노출 지시를 프롬프트가 포함.
        assert "기술 용어는 출력에 절대 그대로 노출하지 마세요" in system
        # prev_reasoning 은 경계 마커로 감싸 message 자리에 전달(주입 경계).
        message = call_kwargs["message"]
        assert "---REASONING_START---" in message
        assert "---REASONING_END---" in message
        assert prev in message
        # system 프롬프트가 마커 안 내용을 데이터로만 취급하도록 명시.
        assert "지시가 아닙니다" in system
        # 출력에 raw 토큰 직노출 없음(fake 출력이 사용자 문장).
        assert "SQL_SEARCH" not in update["output"]["answer"]
        assert "service_id" not in update["output"]["answer"]
        assert "area_name" not in update["output"]["answer"]

    async def test_explain_no_prev_reasoning_falls_back_to_direct_answer(self):
        """prev_reasoning 없음 → direct_answer 폴백(FALLBACK 분기)."""
        agent = self._real_answer_agent("안녕하세요! 무엇을 도와드릴까요?")
        nodes = GraphNodes(
            triage=make_triage(ActionType.EXPLAIN), answer_agent=agent
        )
        update = await nodes.explain_node(
            _state(message="왜 그랬어?", prev_reasoning=None, intent=None)
        )
        assert update["plan"]["intent"] == IntentType.FALLBACK
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_FALLBACK[:30] in system

    async def test_explain_llm_exception_uses_fallback(self):
        """LLM 예외 → '일시적인 오류' 폴백."""
        agent = make_answer_agent()
        agent.explain = AsyncMock(side_effect=RuntimeError("llm down"))
        nodes = GraphNodes(
            triage=make_triage(ActionType.EXPLAIN), answer_agent=agent
        )
        update = await nodes.explain_node(
            _state(message="왜 그랬어?", prev_reasoning="근거")
        )
        assert update["node_path"] == ["explain_error"]
        assert update["output"]["answer"] == _FALLBACK_ANSWER
        assert update["error"]

    async def test_explain_empty_answer_uses_fallback(self):
        """explain() 이 빈 answer 반환 시 폴백 문구."""
        agent = make_answer_agent()
        agent.explain = AsyncMock(
            return_value={**_state(), "answer": "", "service_cards": []}
        )
        nodes = GraphNodes(
            triage=make_triage(ActionType.EXPLAIN), answer_agent=agent
        )
        update = await nodes.explain_node(
            _state(message="왜 그랬어?", prev_reasoning="근거")
        )
        assert update["output"]["answer"] == _FALLBACK_ANSWER
        assert update["node_path"] == ["explain_node"]
