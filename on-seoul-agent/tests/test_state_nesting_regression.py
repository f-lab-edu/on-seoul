"""AgentState 도메인 중첩 리팩터 — 행동 무변경 회귀 가드.

이 파일은 평면 45필드 → 도메인 중첩 10채널 전환이 동작을 1도 바꾸지 않음을 봉인한다.
핵심 가드:
  1. 각 라우트 결과가 새 경로(state["sql"]["results"] 등)에 동일 적재.
  2. filters 머지: router set → retry_prep 1개 드롭 시 잔여 보존.
  3. emit 머지: decision_emitted 보존, searching/answering 만 리셋.
  4. plan 머지(최우선): forced 재시도 후 vector_sub_intent/secondary_intent 보존.
  5. SSE 이벤트 시퀀스·decision 1회·answering 1회 동일.
  6. retry 후 그룹 {} 리셋.
  7. 팬아웃: 두 노드 한 super-step 강제 시 InvalidUpdateError 없음(그래프 직접 조립).
"""

from unittest.mock import MagicMock, patch

import pytest
from langgraph.graph import END, START, StateGraph

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from schemas.state import (
    ActionType,
    AgentState,
    IntentType,
    dict_merge_reducer,
)
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_ai_session,
    make_router,
    make_sql_agent,
    make_triage_router,
    run_graph,
    stream_graph,
)


# ---------------------------------------------------------------------------
# 1. dict_merge_reducer 단위 (filters·emit·plan 공통 머신러리)
# ---------------------------------------------------------------------------


class TestDictMergeReducer:
    def test_empty_new_keeps_old(self):
        assert dict_merge_reducer({"a": 1}, {}) == {"a": 1}
        assert dict_merge_reducer({"a": 1}, None) == {"a": 1}

    def test_none_old_starts_fresh(self):
        assert dict_merge_reducer(None, {"a": 1}) == {"a": 1}

    def test_partial_keys_merge_not_replace(self):
        """서로 다른 키를 다른 super-step 에 써도 누적된다(wholesale 아님)."""
        merged = dict_merge_reducer({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert merged == {"a": 1, "b": 3, "c": 4}

    def test_explicit_none_clears_key(self):
        """값을 명시적 None 으로 보내면 그 키만 덮어 None 으로 비운다."""
        assert dict_merge_reducer({"a": 1, "b": 2}, {"a": None}) == {
            "a": None,
            "b": 2,
        }


# ---------------------------------------------------------------------------
# 2. plan 머지 회귀 가드 (최우선) — forced 재시도 sticky 보존
# ---------------------------------------------------------------------------


class TestPlanMergeStickyOnForcedRetry:
    def _nodes(self) -> GraphNodes:
        return GraphNodes(
            router=make_router(IntentType.VECTOR_SEARCH),
            sql_agent=make_sql_agent([])[0],
            vector_agent=MagicMock(),
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )

    async def test_forced_intent_only_writes_intent_preserving_sticky(self):
        """router forced 경로는 {intent}만 쓴다 → vector_sub_intent/secondary 보존.

        평면 모델의 sticky 동등성: router 가 처음 4필드를 plan 에 set 한 뒤
        retry_prep 가 refined_query 만 비우고, forced 재진입이 intent 만 덮어도
        vector_sub_intent / secondary_intent 가 살아남아야 한다(dict_merge 보존).
        """
        nodes = self._nodes()

        # 1) router 가 plan 4필드를 set (시뮬레이션: dict_merge 적용된 누적 plan).
        plan_after_router = dict_merge_reducer(
            {},
            {
                "intent": IntentType.VECTOR_SEARCH,
                "refined_query": "강남구 테니스장",
                "vector_sub_intent": "identification",
                "secondary_intent": IntentType.SQL_SEARCH,
            },
        )

        # 2) retry_prep 가 plan 에 {"refined_query": None} 만 보낸다(머지).
        plan_after_retry = dict_merge_reducer(
            plan_after_router, {"refined_query": None}
        )
        assert plan_after_retry["refined_query"] is None
        # sticky 필드는 보존되어야 한다.
        assert plan_after_retry["vector_sub_intent"] == "identification"
        assert plan_after_retry["secondary_intent"] == IntentType.SQL_SEARCH

        # 3) forced router 재진입이 {intent} 만 쓴다.
        forced = await nodes.router_node(
            {
                **make_agent_state(),
                "forced_intent": IntentType.VECTOR_SEARCH,
                "plan": plan_after_retry,
            }
        )
        plan_final = dict_merge_reducer(plan_after_retry, forced["plan"])
        # intent 는 갱신, sticky 필드는 여전히 보존(행동 무변경 핵심).
        assert plan_final["intent"] == IntentType.VECTOR_SEARCH
        assert plan_final["vector_sub_intent"] == "identification"
        assert plan_final["secondary_intent"] == IntentType.SQL_SEARCH
        assert "forced_intent" in forced and forced["forced_intent"] is None


# ---------------------------------------------------------------------------
# 3. filters 머지: retry_prep 1개 드롭 시 잔여 보존 (ANALYTICS 부분 드롭)
# ---------------------------------------------------------------------------


class TestFiltersMerge:
    def _nodes(self) -> GraphNodes:
        return GraphNodes(
            router=make_router(IntentType.ANALYTICS),
            sql_agent=MagicMock(),
            vector_agent=MagicMock(),
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )

    async def test_analytics_retry_drops_one_filter_preserving_rest(self):
        nodes = self._nodes()
        state = make_agent_state(
            intent=IntentType.ANALYTICS,
            retry_count=0,
            service_status="접수중",
            area_name="강남구",
            max_class_name="체육시설",
        )
        update = await nodes.retry_prep_node(state)
        # retry_prep 는 service_status 만 드롭(부분 머지) — area/max 는 미포함.
        assert update["filters"] == {"service_status": None}
        merged = dict_merge_reducer(state["filters"], update["filters"])
        # 머지 결과: status 만 None, 나머지 보존.
        assert merged["service_status"] is None
        assert merged["area_name"] == "강남구"
        assert merged["max_class_name"] == "체육시설"


# ---------------------------------------------------------------------------
# 4. emit 머지: decision_emitted 보존, searching/answering 만 리셋
# ---------------------------------------------------------------------------


class TestEmitMerge:
    async def test_retry_prep_resets_progress_but_keeps_decision(self):
        nodes = GraphNodes(
            router=make_router(IntentType.VECTOR_SEARCH),
            sql_agent=MagicMock(),
            vector_agent=MagicMock(),
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )
        # triage/router 가 decision_emitted=True + searching/answering=True 로 누적.
        emit_before = {
            "decision_emitted": True,
            "searching_emitted": True,
            "answering_emitted": True,
        }
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH, retry_count=0
        )
        state["emit"] = dict(emit_before)
        update = await nodes.retry_prep_node(state)
        # retry_prep 는 progress 두 개만 False 로 보낸다(decision 미포함).
        assert update["emit"] == {
            "searching_emitted": False,
            "answering_emitted": False,
        }
        merged = dict_merge_reducer(emit_before, update["emit"])
        # decision 은 전체 실행 1회 — 보존. progress 만 리셋.
        assert merged["decision_emitted"] is True
        assert merged["searching_emitted"] is False
        assert merged["answering_emitted"] is False


# ---------------------------------------------------------------------------
# 5. 라우트 결과가 새 경로에 동일 적재 (SQL/VECTOR/MAP/ANALYTICS)
# ---------------------------------------------------------------------------


class TestRouteResultsLandInNewPaths:
    async def test_sql_route_fills_sql_channel(self):
        rows = [{"service_id": "S1", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("안내"),
        )
        with patch(
            "agents.hydration_node.hydrate_services",
            MagicMock(return_value=rows),
        ):
            result = await run_graph(
                graph,
                make_agent_state(),
                data_session=data_session,
                ai_session=make_ai_session(),
            )
        assert result["plan"]["intent"] == IntentType.SQL_SEARCH
        assert result["sql"]["results"] == rows

    async def test_analytics_route_fills_analytics_channel(self):
        from tests.helpers import make_analytics_agent

        rows = [{"group_value": "강남구", "count": 5}]
        analytics_agent, data_session = make_analytics_agent(rows, group_by="area_name")
        graph = AgentGraph(
            router=make_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=make_answer_agent("집계"),
        )
        result = await run_graph(
            graph,
            make_agent_state(),
            data_session=data_session,
            ai_session=make_ai_session(),
        )
        assert result["plan"]["intent"] == IntentType.ANALYTICS
        assert result["analytics"]["results"] == rows
        assert result["analytics"]["group_by"] == "area_name"


# ---------------------------------------------------------------------------
# 6. SSE 이벤트 시퀀스 — decision 1회 / answering 1회 (관측 동일성)
# ---------------------------------------------------------------------------


class TestSseEventSequenceUnchanged:
    async def _collect(self, gen):
        return [ev async for ev in gen]

    async def test_retrieve_emits_decision_once_answering_once(self):
        rows = [{"service_id": "S1", "service_name": "수영장"}]
        triage, router = make_triage_router(
            ActionType.RETRIEVE,
            IntentType.SQL_SEARCH,
            refined_query="수영장",
            user_rationale="수영장을 찾으시는군요.",
        )
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            triage=triage,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("안내"),
        )
        with patch(
            "agents.hydration_node.hydrate_services",
            MagicMock(return_value=rows),
        ):
            events = await self._collect(
                stream_graph(
                    graph,
                    make_agent_state(),
                    data_session=data_session,
                    ai_session=make_ai_session(),
                )
            )
        steps = [d.get("step") for t, d in events if t == "progress"]
        decisions = [d for t, d in events if t == "decision"]
        assert decisions and len(decisions) == 1  # decision 전체 1회
        assert steps.count("answering") == 1  # answering 1회
        assert steps[0] == "routing"  # 시퀀스 시작은 routing


# ---------------------------------------------------------------------------
# 7. 팬아웃: sql_node + vector_node 한 super-step 강제 → InvalidUpdateError 없음
# ---------------------------------------------------------------------------


class TestFanoutNoChannelConflict:
    async def test_two_search_nodes_in_one_superstep(self):
        """sql/vector 는 별도 최상위 채널이므로 동일 super-step 병렬 쓰기에도 충돌 없음.

        현재 프로덕션 그래프는 미와이어링이라 E2E 불가 → 그래프를 직접 조립해
        START 에서 두 노드로 동시 팬아웃하고 합류시켜 InvalidUpdateError 부재를 봉인한다.
        """
        sql_agent, sql_data_session = make_sql_agent([{"service_id": "S1"}])
        vector_agent = MagicMock()

        async def _vsearch(state):
            return {
                "vector": {"results": [{"service_id": "V1", "rrf_score": 0.5}]},
                "plan": {"refined_query": "정제"},
            }

        vector_agent.search = _vsearch
        nodes = GraphNodes(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            vector_agent=vector_agent,
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )

        builder: StateGraph = StateGraph(AgentState)
        builder.add_node("sql_node", nodes.sql_node)
        builder.add_node("vector_node", nodes.vector_node)
        builder.add_node("join", lambda s: {"node_path": ["join"]})
        # START → [sql_node, vector_node] 병렬 팬아웃 → join 합류.
        builder.add_edge(START, "sql_node")
        builder.add_edge(START, "vector_node")
        builder.add_edge("sql_node", "join")
        builder.add_edge("vector_node", "join")
        builder.add_edge("join", END)
        compiled = builder.compile()

        from tests.helpers import patch_node_sessions

        state = make_agent_state(intent=IntentType.SQL_SEARCH)
        with patch_node_sessions(
            data_session=sql_data_session, ai_session=make_ai_session()
        ):
            # InvalidUpdateError 가 나면 여기서 예외 → 테스트 실패.
            result = await compiled.ainvoke(state, config={"recursion_limit": 10})

        # 두 채널 모두 독립적으로 적재됨(공통 부모 충돌 없음).
        assert result["sql"]["results"] == [{"service_id": "S1"}]
        assert result["vector"]["results"] == [{"service_id": "V1", "rrf_score": 0.5}]


# ---------------------------------------------------------------------------
# 8. retry_prep 4케이스(A/B/C/D) 그룹 리셋 정합 — 실제 노드 출력 단언 (QA 보강)
#
# 기존 가드는 dict_merge_reducer 를 손으로 호출해 누적을 검증한다.
# 여기서는 retry_prep_node 의 *실제 반환 dict* 가 케이스별로 올바른 중첩 채널을
# 리셋/보존하는지(새 경로) 직접 단언한다. 행동 무변경의 핵심 — 평면 모델의
# 필드별 None 나열을 그룹 {} 리셋으로 바꾼 재작성이 동등함을 봉인.
# ---------------------------------------------------------------------------


class TestRetryPrepGroupResetPerCase:
    def _nodes(self, intent: IntentType) -> GraphNodes:
        return GraphNodes(
            router=make_router(intent),
            sql_agent=MagicMock(),
            vector_agent=MagicMock(),
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )

    async def test_case_a_forced_resets_search_groups_and_clears_filters(self):
        """A(전환): sql/vector/map/hydration={} 통째 리셋, plan 은 refined_query 만 비움."""
        nodes = self._nodes(IntentType.SQL_SEARCH)  # SQL → 전환 fallback 존재
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            vector_sub_intent="identification",
            secondary_intent=IntentType.VECTOR_SEARCH,
            refined_query="강남 수영장",
            area_name="강남구",
            max_class_name="체육시설",
            retry_count=0,
        )
        update = await nodes.retry_prep_node(state)
        # 검색/하이드 그룹은 빈 dict 로 통째 리셋(reducer 없음 → wholesale).
        assert update["sql"] == {}
        assert update["vector"] == {}
        assert update["map"] == {}
        assert update["hydration"] == {}
        # plan 머지: refined_query 만 비우고 sticky 는 전송하지 않음(보존).
        assert update["plan"] == {"refined_query": None}
        # filters 는 전부 드롭(머지로 None).
        assert update["filters"] == {
            "max_class_name": None,
            "area_name": None,
            "service_status": None,
            "payment_type": None,
        }
        # 평면 forced_intent 세팅, emit progress 만 리셋.
        assert "forced_intent" in update and update["forced_intent"] is not None
        assert update["emit"] == {
            "searching_emitted": False,
            "answering_emitted": False,
        }
        # 실제 reducer 누적: sticky 가 plan 에서 살아남는가(행동 무변경 핵심).
        plan_after = dict_merge_reducer(state["plan"], update["plan"])
        assert plan_after["refined_query"] is None
        assert plan_after["vector_sub_intent"] == "identification"
        assert plan_after["secondary_intent"] == IntentType.VECTOR_SEARCH
        assert plan_after["intent"] == IntentType.SQL_SEARCH

    async def test_case_b_analytics_drops_one_filter_only_and_resets_analytics(self):
        """B(ANALYTICS): analytics={} 만 리셋, sql/vector/hydration 미포함, 필터 1개만 드롭."""
        nodes = self._nodes(IntentType.ANALYTICS)
        state = make_agent_state(
            intent=IntentType.ANALYTICS,
            service_status="접수중",
            area_name="강남구",
            max_class_name="체육시설",
            retry_count=0,
        )
        update = await nodes.retry_prep_node(state)
        assert update["analytics"] == {}
        # 다른 검색 그룹은 건드리지 않는다(analytics 단일 소유 채널).
        assert "sql" not in update
        assert "vector" not in update
        assert "hydration" not in update
        assert "map" not in update
        # 가장 제약 큰 필터 1개만 드롭(부분 머지) — 나머지는 미전송(보존).
        assert update["filters"] == {"service_status": None}

    async def test_case_c_relax_resets_search_groups_clears_filters(self):
        """C(완화): VECTOR 0건 등 — sql/vector/map/hydration={} 리셋 + 필터 전부 드롭."""
        nodes = self._nodes(IntentType.VECTOR_SEARCH)  # VECTOR 는 전환 fallback 없음
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            payment_type="무료",
            area_name="강남구",
            retry_count=0,
        )
        update = await nodes.retry_prep_node(state)
        assert update["sql"] == {}
        assert update["vector"] == {}
        assert update["map"] == {}
        assert update["hydration"] == {}
        assert update["plan"] == {"refined_query": None}
        assert update["filters"] == {
            "max_class_name": None,
            "area_name": None,
            "service_status": None,
            "payment_type": None,
        }
        # 전환이 아니므로 forced_intent 는 세팅하지 않는다.
        assert "forced_intent" not in update

    async def test_case_d_map_expands_radius_and_resets_only_map(self):
        """D(MAP): map={} + retry_radius_m 만, sql/vector/hydration 은 미포함(불필요)."""
        nodes = self._nodes(IntentType.MAP)
        state = make_agent_state(intent=IntentType.MAP, retry_count=0)
        update = await nodes.retry_prep_node(state)
        assert update["map"] == {}
        assert update["retry_radius_m"] is not None and update["retry_radius_m"] > 0
        # MAP 경로는 sql/vector/hydration 슬롯을 채우지 않으므로 리셋 불필요.
        assert "sql" not in update
        assert "vector" not in update
        assert "hydration" not in update
        # MAP 은 정형 필터 드롭도 하지 않는다(반경만 확장).
        assert "filters" not in update


# ---------------------------------------------------------------------------
# 9. 전체 시퀀스: retry_prep → forced router_node 재진입 sticky 보존 (노드 실제 출력)
#
# 앞선 plan 머지 테스트가 step 1 을 손으로 시뮬레이션한 것과 달리, 여기서는
# retry_prep_node 와 forced router_node 의 *실제 반환 dict* 를 순차로 reducer 에
# 흘려 vector_sub_intent/secondary_intent 가 끝까지 생존함을 봉인한다.
# ---------------------------------------------------------------------------


class TestForcedRetrySequenceEndToEndSticky:
    async def test_retry_prep_then_forced_router_preserves_sticky(self):
        nodes = GraphNodes(
            router=make_router(IntentType.VECTOR_SEARCH),
            sql_agent=MagicMock(),
            vector_agent=MagicMock(),
            answer_agent=make_answer_agent(),
            analytics_agent=MagicMock(),
        )
        # router 가 처음 4필드를 set 한 누적 plan 상태로 시작(SQL 1차 시도).
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            refined_query="강남 테니스장",
            vector_sub_intent="identification",
            secondary_intent=IntentType.SQL_SEARCH,
            retry_count=0,
        )

        # 1) retry_prep 의 실제 출력으로 plan 채널 갱신.
        rp = await nodes.retry_prep_node(state)
        plan_after_retry = dict_merge_reducer(state["plan"], rp["plan"])
        assert plan_after_retry["refined_query"] is None
        assert plan_after_retry["vector_sub_intent"] == "identification"
        assert plan_after_retry["secondary_intent"] == IntentType.SQL_SEARCH
        # 평면 forced_intent 가 retry_prep 출력에 세팅됐는가.
        forced_intent = rp["forced_intent"]
        assert forced_intent is not None

        # 2) forced router_node 재진입 — 실제 노드가 {intent} 만 쓰는지.
        next_state = {
            **state,
            "forced_intent": forced_intent,
            "plan": plan_after_retry,
        }
        ro = await nodes.router_node(next_state)
        assert ro["plan"] == {"intent": forced_intent}  # intent 만 기록
        assert ro["forced_intent"] is None  # 1회성 소비

        # 3) 최종 누적: intent 는 forced 로 갱신, sticky 는 끝까지 생존.
        plan_final = dict_merge_reducer(plan_after_retry, ro["plan"])
        assert plan_final["intent"] == forced_intent
        assert plan_final["vector_sub_intent"] == "identification"
        assert plan_final["secondary_intent"] == IntentType.SQL_SEARCH
        assert plan_final["refined_query"] is None


@pytest.fixture(autouse=True)
def _no_redis_required(monkeypatch):
    """refine/answer 캐시는 redis=None 일 때 fail-open. 경고 로그만 — 동작 무영향."""
    yield
