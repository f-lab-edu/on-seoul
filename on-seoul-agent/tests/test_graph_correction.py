"""AgentGraph 자기 교정(self-correction) 사이클 테스트.

test_graph.py 분할 산출 — TestSelfCorrectionCycle /
TestSelfCorrectionInfiniteLoopRegression / TestDirectedSelfCorrectionRetry.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent
from agents.graph import AgentGraph
from agents.nodes import (
    GraphNodes,
    _ANALYTICS_DROP_ORDER,
    _MAP_RETRY_RADIUS_M,
)
from agents.router_agent import RouterAgent, _IntentOutput
from schemas.intake import IntakeAction, TurnKind
from schemas.state import IntentType
from tests.helpers import make_intake, patch_node_sessions, run_graph
from tests._graph_support import (
    _ai_session,
    _answer_agent,
    _router,
    _sql_agent,
    _state,
    _vector_agent,
)


# ---------------------------------------------------------------------------
# 3. 자기 교정(Self-Correction) 사이클 테스트
# ---------------------------------------------------------------------------


class TestSelfCorrectionCycle:
    async def test_empty_answer_triggers_retry(self):
        """answer가 빈 문자열이면 retry_count=0일 때 재검색(router로 복귀)을 시도한다."""
        rows = [{"service_id": "S001", "service_name": "수영장"}]
        sql_agent, data_session = _sql_agent(rows)

        # 첫 번째 호출은 빈 답변, 두 번째 호출은 정상 답변 반환
        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        answer_chain.ainvoke = AsyncMock(
            side_effect=[
                "",  # 첫 번째: 빈 답변 → 재시도 트리거
                "재검색 후 답변",  # 두 번째: 정상 답변
            ]
        )
        agent._answer_chain = answer_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=agent,
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # 재시도 후 최종 답변이 채워져야 한다
        assert result["output"]["answer"] == "재검색 후 답변"
        assert result["retry_count"] == 1

    async def test_self_correction_max_one_retry(self):
        """자기 교정은 최대 1회만 수행한다 (retry_count >= 1이면 trace_node로 진행)."""
        _, data_session = _sql_agent([])

        agent = AnswerAgent.__new__(AnswerAgent)
        answer_chain = MagicMock()
        # 두 번 모두 빈 답변 반환 — 두 번째는 trace로 진행해야 한다
        answer_chain.ainvoke = AsyncMock(return_value="")
        agent._answer_chain = answer_chain

        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=_sql_agent([])[0],
            answer_agent=agent,
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # 무한 루프 없이 종료, retry_count는 1
        assert result["retry_count"] == 1

    async def test_error_state_with_fallback_answer_skips_retry(self):
        """router 예외 시 fallback_answer가 설정되므로 재시도 없이 trace_node로 진행한다.

        needs_retry = not answer.strip() and retry_count == 0
        error + fallback_answer 조합은 이미 최선의 응답이므로 재시도 불필요.
        """
        _, data_session = _sql_agent([])

        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        # router가 예외를 던지면 _router_node 핸들러가 fallback_answer를 주입한다.
        structured.ainvoke = AsyncMock(
            side_effect=[
                RuntimeError("일시적 LLM 오류"),
                _IntentOutput(intent=IntentType.FALLBACK),
            ]
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent("재시도 후 답변"),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # fallback_answer가 설정되어 재시도 없이 종료 — retry_count는 0 유지
        assert result["retry_count"] == 0
        assert result["output"]["answer"] is not None
        assert len(result["output"]["answer"]) > 0


# ---------------------------------------------------------------------------
# 4. AgentState 입출력 계약 (workflow.py와 동일)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 7. Self-Correction 무한 루프 회귀 테스트 (가설 검증)
# ---------------------------------------------------------------------------


class TestSelfCorrectionInfiniteLoopRegression:
    """router_error 경로에서 is_retry 탐지 실패로 retry_count가 0으로 고정되는 버그 회귀 방지.

    _router_node 예외 시 _node_path에 "router_error"만 추가되므로 is_retry("router" in path)가
    False를 반환한다. recursion_limit=10으로 무한 루프를 차단하고, 예외 핸들러가 fallback answer를
    주입하여 _self_correction_edge의 `not answer.strip()` 조건을 False로 만들어 종료한다.
    """

    async def test_router_always_failing_terminates_without_recursion_error(self):
        """router 가 예외를 던지면 fallback answer 가 설정되어 1 cycle 만에 종료된다.

        실제 동작: _router_node 예외 핸들러가 fallback answer 를 state 에 주입하므로
        _self_correction_edge 의 `not answer.strip()` 조건이 False 가 되어
        GraphRecursionError 없이 trace_node 로 즉시 이동한다.
        router_error 는 1회만 node_path 에 기록된다.
        """
        _, data_session = _sql_agent([])

        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        structured.ainvoke = AsyncMock(side_effect=_raise)
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        graph = AgentGraph(
            intake=make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE),
            router=router_agent,
            answer_agent=_answer_agent("불릴 일 없는 답"),
        )

        state = {**_state(), "retry_count": 0, "node_path": [], "started_at": None}

        result = await graph._compiled_graph.ainvoke(
            state,
            config={
                "recursion_limit": 8,
                "configurable": {
                    "data_session": data_session,
                    "ai_session": _ai_session(),
                },
            },
        )

        # fallback answer 가 설정되어 정상 종료된다.
        assert result["output"]["answer"], "fallback answer 가 비어있으면 안 된다"
        # router_error 는 1회만 기록된다 (무한 사이클 없음).
        # intake 는 정상 분류(NEW+RETRIEVE)하고 router_node 에서 예외가 난다.
        error_count = result["node_path"].count("router_error")
        assert error_count == 1, (
            f"router_error 가 1회 초과 기록됨: {result['node_path']}"
        )

    async def test_retry_prep_node_increments_retry_count_and_clears_results(self):
        """retry_prep_node가 retry_count를 1 증가시키고 이전 검색 결과를 초기화한다.

        재시도 제어는 retry_count 단일 필드로 자기 완결되며,
        _node_path 기반 재진입 감지에 의존하지 않는다.
        """
        graph = AgentGraph(answer_agent=_answer_agent())

        stale_state = _state(
            retry_count=0,
            sql_results=[{"service_id": "S001"}],
            vector_results=[{"service_id": "S002"}],
            map_results={"type": "FeatureCollection"},
            refined_query="테니스장",
            error="이전 에러",
        )

        result = await graph._nodes.retry_prep_node(stale_state)

        # retry_count 증가
        assert result["retry_count"] == 1
        # 이전 검색 결과 그룹 통째 리셋({}) + refined_query 머지 None + error 초기화
        assert result["sql"] == {}
        assert result["vector"] == {}
        assert result["map"] == {}
        assert result["plan"]["refined_query"] is None
        assert result["error"] is None
        # node_path 기록 (반환 dict 누적분)
        assert "retry_prep" in result["node_path"]

    async def test_self_correction_edge_skips_retry_when_answer_present(self):
        """answer가 있으면 error 유무와 무관하게 trace_node로 진행한다.

        needs_retry = not answer.strip() and retry_count == 0
        — error + fallback_answer 조합은 재시도 불필요.
        """
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )

        # answer 있고 error 있는 state — 수정 후: trace_node 로 바로 진행
        state_with_error = _state(
            answer="fallback message",
            error="still failing",
            retry_count=0,
        )

        # 수정 후: answer.strip() 이 truthy 이므로 needs_retry=False → end_normal
        assert graph._nodes.self_correction_edge(state_with_error) == "end_normal"

        # answer 없을 때만 retry 트리거
        state_empty_answer = _state(
            answer="",
            error=None,
            retry_count=0,
        )
        assert (
            graph._nodes.self_correction_edge(state_empty_answer) == "retry_prep_node"
        )

        # retry_count >= 1 이면 항상 end_normal
        state_after_retry = {**state_empty_answer, "retry_count": 1}
        assert graph._nodes.self_correction_edge(state_after_retry) == "end_normal"

    async def test_self_correction_edge_zero_hits_triggers_retry(self):
        """SQL/VECTOR 하드 필터 0건이면 answer가 있어도 1회 재시도(안전망)."""
        graph = AgentGraph(answer_agent=_answer_agent())

        zero_hits = _state(
            intent=IntentType.SQL_SEARCH,
            answer="조건에 맞는 결과가 없어요.",
            hydrated_services=[],
            sql_results=[],
            vector_results=None,
            retry_count=0,
        )
        assert graph._nodes.self_correction_edge(zero_hits) == "retry_prep_node"

    async def test_self_correction_edge_zero_hits_capped_after_retry(self):
        """0건이라도 retry_count>=1이면 무한루프 방지 — end_normal."""
        graph = AgentGraph(answer_agent=_answer_agent())
        zero_hits = _state(
            intent=IntentType.VECTOR_SEARCH,
            answer="결과 없음",
            hydrated_services=[],
            retry_count=1,
        )
        assert graph._nodes.self_correction_edge(zero_hits) == "end_normal"

    async def test_self_correction_edge_zero_hits_only_for_search_intents(self):
        """FALLBACK 등 비검색 intent는 0건이어도 재시도하지 않는다."""
        graph = AgentGraph(answer_agent=_answer_agent())
        state = _state(
            intent=IntentType.FALLBACK,
            answer="안내드립니다.",
            hydrated_services=[],
            retry_count=0,
        )
        assert graph._nodes.self_correction_edge(state) == "end_normal"

    async def test_self_correction_edge_with_hits_no_retry(self):
        """결과가 있으면 재시도하지 않는다."""
        graph = AgentGraph(answer_agent=_answer_agent())
        state = _state(
            intent=IntentType.SQL_SEARCH,
            answer="5건 안내",
            hydrated_services=[{"service_id": "S1"}],
            retry_count=0,
        )
        assert graph._nodes.self_correction_edge(state) == "end_normal"

    async def test_retry_prep_node_relaxes_payment_and_sets_flag(self):
        """retry_prep_node가 payment_type을 드롭하고 retry_relaxed=True를 세팅한다."""
        graph = AgentGraph(answer_agent=_answer_agent())

        stale = _state(
            retry_count=0,
            payment_type="무료",
            hydrated_services=[],
        )
        result = await graph._nodes.retry_prep_node(stale)
        assert result["filters"]["payment_type"] is None
        assert result["retry_relaxed"] is True

    async def test_router_node_propagates_payment_type(self):
        """router_node 반환 update에 payment_type이 포함된다."""
        router = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.SQL_SEARCH,
                refined_query="강남구 무료 문화행사",
                max_class_name="문화체험",
                area_name="강남구",
                payment_type="무료",
            )
        )
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router._llm = llm

        graph = AgentGraph(router=router, answer_agent=_answer_agent())
        update = await graph._nodes.router_node(_state(message="강남구 무료 문화행사"))
        assert update["filters"]["payment_type"] == "무료"

    # payment_type=None 생략은 test_router_node_omits_postfilter_when_none 의
    # None-생략 분기와 동일 로직(필드 순열)이라 축소했다. payment_type 전파(충돌 방지
    # 입력)는 위 test_router_node_propagates_payment_type 가 유지한다.


# ---------------------------------------------------------------------------
# 7a-bis. 방향성 self-correction 재시도 (forced_intent / ANALYTICS 드롭 / MAP 반경)
# ---------------------------------------------------------------------------


# payment_type=None 생략은 test_router_node_omits_postfilter_when_none 의
# None-생략 분기와 동일 로직(필드 순열)이라 축소했다. payment_type 전파(충돌 방지
# 입력)는 위 test_router_node_propagates_payment_type 가 유지한다.


# ---------------------------------------------------------------------------
# 7a-bis. 방향성 self-correction 재시도 (forced_intent / ANALYTICS 드롭 / MAP 반경)
# ---------------------------------------------------------------------------


class TestDirectedSelfCorrectionRetry:
    """방향성 재시도: SQL→VECTOR 강제 전환, ANALYTICS 필터 드롭, MAP 반경 확장."""

    def _nodes(self) -> GraphNodes:
        return AgentGraph(answer_agent=_answer_agent())._nodes

    # ── 강제 전환 (3a~3c) ──

    async def test_retry_prep_sql_forces_vector_and_clears_filters(self):
        """SQL_SEARCH 0건 재시도 → forced_intent=VECTOR_SEARCH, 정형 필터 전부 None."""
        nodes = self._nodes()
        stale = _state(
            intent=IntentType.SQL_SEARCH,
            retry_count=0,
            max_class_name="체육시설",
            area_name="강남구",
            service_status="접수중",
            payment_type="무료",
            sql_results=[],
        )
        result = await nodes.retry_prep_node(stale)
        assert result["forced_intent"] == IntentType.VECTOR_SEARCH
        assert result["retry_count"] == 1
        for f in ("max_class_name", "area_name", "service_status", "payment_type"):
            assert result["filters"][f] is None
        assert result["retry_relaxed"] is True
        assert "retry_prep" in result["node_path"]

    # forced_intent → classify 미호출/소비 단위 검증은
    # test_graph_triage.TestRouterNodeStatePropagation.test_router_node_honors_forced_intent
    # 가 동일하게 커버하므로 여기선 축소했다(e2e 전환 경로는 아래 유지).

    async def test_e2e_sql_zero_hits_switches_to_vector(self):
        """SQL_SEARCH 0건 시나리오 → retry_prep → router(forced) → vector_node 경로 전환."""
        sql_agent, data_session = _sql_agent([])  # SQL 0건
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        vrows = [{"service_id": "V9", "service_name": "체험관", "similarity": 0.8}]
        hydrated = [{"service_id": "V9", "service_name": "체험관"}]

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
                router=_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=data_session,
                ai_session=ai_session,
            )

        path = result["node_path"]
        assert "sql_node" in path
        assert "retry_prep" in path
        assert "vector_node" in path
        assert (
            path.index("sql_node")
            < path.index("retry_prep")
            < path.index("vector_node")
        )
        assert result["retry_count"] == 1

    # ── ANALYTICS 완화 (3c~3d) ──

    def test_analytics_zero_hits_predicate(self):
        # B3-2: facade zero-hit staticmethod 퇴역 — CorrectionNodes 경유로 호출.
        corr = self._nodes()._correction
        assert corr._analytics_zero_hits(_state(analytics_results=[])) is True
        assert corr._analytics_zero_hits(_state(analytics_results=None)) is True
        assert (
            corr._analytics_zero_hits(
                _state(analytics_results=[{"x": 1}], error="boom")
            )
            is True
        )
        assert corr._analytics_zero_hits(_state(analytics_results=[{"x": 1}])) is False

    # ANALYTICS zero-hits → retry_prep edge 는 generic test_self_correction_edge_zero_hits_triggers_retry
    # 와 동일 edge 로직의 intent 순열이고, ANALYTICS 고유 predicate 는
    # test_analytics_zero_hits_predicate + e2e(test_e2e_analytics_zero_hits_drops_status_filter)가
    # 독립 커버하므로 축소했다.

    # ANALYTICS retry_count=1 cap → end_normal 은 intent 무관 동일 cap 불변식이라
    # (test_self_correction_max_one_retry / test_self_correction_edge_zero_hits_capped_after_retry
    # 가 이미 고정) 값만 다른 순열로 축소했다.

    async def test_retry_prep_analytics_drops_status_first(self):
        """effective 필터 우선순위: status 가 1순위. keyword 는 드롭 대상이 아니다.

        analytics_keyword 는 LLM 이 message 에서 재추출하는 출력 전용 슬롯이라
        state 드롭이 무효 → _ANALYTICS_DROP_ORDER 에서 제외. keyword 보유 분석
        질의여도 곧장 실효성 있는 service_status 를 드롭해야 한다.
        """
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            retry_count=0,
            analytics_keyword="따릉이",
            service_status="접수중",
            area_name="강남구",
            max_class_name="체육시설",
        )
        result = await nodes.retry_prep_node(state)
        # keyword 는 드롭 대상 아님 — service_status 가 1순위로 드롭됨(filters 머지).
        assert "keyword" not in result["filters"]
        assert result["filters"]["service_status"] is None
        # area/max_class 는 유지(filters 머지에 미포함)
        assert "area_name" not in result["filters"]
        assert "max_class_name" not in result["filters"]
        assert result["analytics"] == {}

    async def test_retry_prep_analytics_drops_area_when_no_status(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            retry_count=0,
            analytics_keyword=None,
            service_status=None,
            area_name="강남구",
        )
        result = await nodes.retry_prep_node(state)
        assert result["filters"]["area_name"] is None
        assert "service_status" not in result["filters"]

    async def test_retry_prep_analytics_no_filter_to_drop(self):
        nodes = self._nodes()
        state = _state(intent=IntentType.ANALYTICS, retry_count=0)
        result = await nodes.retry_prep_node(state)
        # 드롭할 필터 없음 — analytics 그룹만 리셋({}), filters 머지 미발생
        assert result["analytics"] == {}
        assert "filters" not in result
        for f in _ANALYTICS_DROP_ORDER:
            assert f not in result.get("filters", {})

    # ANALYTICS zero-hits 재시도 E2E 는 retry 사이클 wiring(node 2회 실행)을
    # test_e2e_sql_zero_hits_switches_to_vector 가, status-우선 필터 드롭을
    # test_retry_prep_analytics_drops_status_first 가 독립 커버하므로
    # retry-boundary 통합 중복으로 축소했다.

    # ── MAP 반경 확장 (C1, 3c~3d) ──

    def test_map_zero_hits_predicate(self):
        # B3-2: facade zero-hit staticmethod 퇴역 — CorrectionNodes 경유로 호출.
        corr = self._nodes()._correction
        assert corr._map_zero_hits(_state(map_results=None)) is False
        assert (
            corr._map_zero_hits(
                _state(map_results={"type": "FeatureCollection", "features": []})
            )
            is True
        )
        assert (
            corr._map_zero_hits(
                _state(
                    map_results={
                        "type": "FeatureCollection",
                        "features": [{"type": "Feature"}],
                    }
                )
            )
            is False
        )

    # MAP zero-hits → retry_prep edge 도 generic edge 로직의 intent 순열이라 축소했다.
    # MAP 고유 분기(좌표 없음 → no retry, radius 확장)는 아래 전용 테스트가 유지한다.

    def test_self_correction_edge_map_no_coords_no_retry(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            answer="위치를 알려주세요",
            map_results=None,
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "end_normal"

    # MAP retry_count=1 cap → end_normal 도 intent 무관 동일 cap 불변식이라 축소했다.

    async def test_retry_prep_map_expands_radius(self):
        nodes = self._nodes()
        state = _state(
            intent=IntentType.MAP,
            retry_count=0,
            map_results={"type": "FeatureCollection", "features": []},
        )
        result = await nodes.retry_prep_node(state)
        assert result["retry_radius_m"] == _MAP_RETRY_RADIUS_M
        assert result["map"] == {}
        assert result["retry_relaxed"] is True

    async def test_map_node_uses_retry_radius(self):
        nodes = self._nodes()
        data_session = MagicMock()
        geojson = {"type": "FeatureCollection", "features": []}
        with (
            patch(
                "agents._ondata_gateway._map_search", AsyncMock(return_value=geojson)
            ) as mock_map,
            patch_node_sessions(data_session=data_session),
        ):
            update = await nodes.map_node(
                _state(user_lat=37.5, user_lng=127.0, retry_radius_m=3000),
            )
        mock_map.assert_awaited_once_with(data_session, 37.5, 127.0, radius_m=3000)
        # ChannelData query_text/parameters 에 확장 반경(3000m)이 반영되어야 한다.
        ch = next(iter(update["search_channels"].values()))
        assert "r=3000m" in ch["query"]["query_text"]
        assert ch["query"]["parameters"]["radius_m"] == 3000

    async def test_map_node_default_radius_when_no_retry(self):
        nodes = self._nodes()
        data_session = MagicMock()
        geojson = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"service_id": "M1"}}],
        }
        with (
            patch(
                "agents._ondata_gateway._map_search", AsyncMock(return_value=geojson)
            ) as mock_map,
            patch_node_sessions(data_session=data_session),
        ):
            update = await nodes.map_node(_state(user_lat=37.5, user_lng=127.0))
        mock_map.assert_awaited_once_with(data_session, 37.5, 127.0, radius_m=1000)
        # ChannelData query_text 에 실제 반경 반영
        ch = next(iter(update["search_channels"].values()))
        assert "r=1000m" in ch["query"]["query_text"]

    # MAP zero-hits 반경 확장 재시도 E2E 도 retry 사이클 wiring 은 SQL E2E 가,
    # 반경 확장 로직은 test_retry_prep_map_expands_radius + test_map_node_uses_retry_radius
    # 가 독립 커버하므로 retry-boundary 통합 중복으로 축소했다.

    # ── 트리거 평가 순서 (C3, 3d) ──

    def test_empty_answer_takes_priority_over_zero_hits(self):
        """빈 답변 ∧ 0건 동시 참 → ② 빈 답변 분기 먼저(여전히 retry_prep)."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            answer="",
            sql_results=[],
            hydrated_services=[],
            retry_count=0,
        )
        # 둘 다 retry 지만, 빈 답변이 intent 평가보다 먼저 매칭되는지 확인.
        assert nodes.self_correction_edge(state) == "retry_prep_node"

    def test_intent_branches_mutually_exclusive(self):
        """한 순회에 하나의 intent 분기만 평가된다(ANALYTICS 0건이어도 MAP 판정 무관)."""
        nodes = self._nodes()
        state = _state(
            intent=IntentType.ANALYTICS,
            answer="결과 없음",
            analytics_results=[{"x": 1}],  # ANALYTICS 0건 아님
            map_results={
                "type": "FeatureCollection",
                "features": [],
            },  # MAP 0건이지만 무시
            retry_count=0,
        )
        assert nodes.self_correction_edge(state) == "end_normal"


# ---------------------------------------------------------------------------
# 7b. Router refined_query 산출 → state 전파 회귀
# ---------------------------------------------------------------------------
