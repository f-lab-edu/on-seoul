"""LangGraph StateGraph 기반 멀티에이전트 워크플로우 ([C] W2 + Answer Cache + SearchPersist).

그래프 구조 ([C] W2):
    START
      ↓
    reference_resolution_node   — 지시 참조 선판정 (규칙 기반, LLM 미사용)
      ├─ referential → rehydrate_node → describe_node → search_persist_node → trace_node
      └─ non-referential → triage_node
           ↓
    triage_node                 — TriageAgent.classify(), action 결정만(action·out_of_scope_type·user_rationale)
      ├─ RETRIEVE     → router_node (RouterAgent.classify(), intent·refined_query·post-filter·secondary_intent)
      │                  → cache_check_node → [sql/vector/map/analytics]
      │                  → hydration_node → rrf_fusion_node → pre_answer_gate_node
      │                       ├─ 0건(C2) → retry_prep_node → router_node 재진입
      │                       └─ 유건    → answer_node
      ├─ DIRECT_ANSWER → direct_answer_node → 종단 체인
      ├─ AMBIGUOUS     → ambiguous_node → 종단 체인
      ├─ OUT_OF_SCOPE  → out_of_scope_node
      │    ├─ domain_outside → 종단 체인
      │    └─ attribute_gap → vector_node → hydration_node → ...
      └─ EXPLAIN       → explain_node → 종단 체인
      ↓
    answer_node                 — AnswerAgent.answer()
      ↓
    (self_correction)           — 비-RETRIEVE는 제외. 빈 답변/0건 시 retry_prep 경유
      ↓ (정상) 또는 사이클
    [retry_prep_node]           — retry_count 증가 + 이전 검색 결과 초기화 → router_node 재진입
    cache_write_node            — 정상 결과만(SQL_SEARCH / VECTOR_SEARCH) 캐시 저장
      ↓
    search_persist_node         — chat_search_queries + chat_search_results 적재 (best-effort)
      ↓
    trace_node                  — chat_agent_traces 저장 (best-effort, 최종 종단 노드)
      ↓
    END

책임 분리:
    노드·엣지 구현  → agents/nodes.py (GraphNodes)
    그래프 조립·실행 → 이 파일 (AgentGraph)

그래프 등록:
    GraphNodes 의 바운드 메서드(노드 함수·라우팅 함수)를 StateGraph 에 직접 등록한다.
    GraphNodes 는 무상태 싱글톤이고 AgentGraph 를 역참조하지 않으므로, 바운드 메서드
    등록으로 인한 순환 참조는 없다(Python GC 가 정상 처리). CompiledGraph 는 인스턴스
    단위로 컴파일한다(컴파일 비용이 저렴해 클래스 수준 캐시는 불필요).

세션 (제안 0-6 — 노드 로컬 세션):
    GraphNodes 는 컨테이너당 싱글톤(무상태)이다. DB 를 쓰는 노드는 노드 내부에서
    `data_session_ctx()`/`ai_session_ctx()` 로 풀에서 세션을 잡고 즉시 반납한다
    (acquire-use-release). 따라서 run()/stream() 은 세션을 주입받지 않으며, 커넥션
    점유가 노드 쿼리 윈도우로 축소되어 answer LLM 스트리밍 동안 커넥션을 잡지 않는다.

    0-1 의 config(`configurable`) 세션 주입은 노드 로컬 세션으로 대체되어 제거됐다.
    세션이 노드 지역 변수로만 존재하므로 요청 간 교차가 원천 차단된다. 요청 격리는
    노드 로컬 세션 + state(node_path/started_at)가 담당한다.
"""

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from agents.analytics_agent import AnalyticsAgent
from agents.answer_agent import AnswerAgent
from agents.nodes import GraphNodes
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.triage_agent import TriageAgent
from agents.vector_agent import VectorAgent
from schemas.events import DecisionEvent
from schemas.state import AgentState

logger = logging.getLogger(__name__)

def _out_of_scope_route(state: AgentState) -> str:
    """out_of_scope_node 직후 — attribute_gap이면 vector_node, domain_outside면 종단 체인.

    GraphNodes 메서드가 아닌 graph.py 모듈 수준 라우팅 함수다(상태만 읽는 순수 함수).
    """
    if state.get("out_of_scope_type") == "attribute_gap":
        return "vector_node"
    return "search_persist_node"


# ---------------------------------------------------------------------------
# 그래프 빌드 (인스턴스당 1회)
# ---------------------------------------------------------------------------


def _build_graph(nodes: GraphNodes) -> Any:
    """StateGraph를 구성하고 컴파일한다. GraphNodes 바운드 메서드를 직접 등록한다.

    그래프 구조 ([C] W2 확장):
    START → reference_resolution_node
      ├─ referential → rehydrate_node → describe_node → search_persist_node → trace_node
      └─ non-referential → triage_node (action 결정)
           │
           ├─ action=RETRIEVE     → router_node (검색 계획) → cache_check_node
           │                           → [sql/vector/map/analytics]
           │                           → hydration_node → pre_answer_gate_node
           │                                ├─ 0건(C2) → retry_prep_node → router_node 재진입
           │                                └─ 유건    → rrf_fusion_node → answer_node
           ├─ action=DIRECT_ANSWER → direct_answer_node → 종단 체인
           ├─ action=AMBIGUOUS     → ambiguous_node → 종단 체인
           ├─ action=OUT_OF_SCOPE  → out_of_scope_node
           │    ├─ domain_outside → 종단 체인
           │    └─ attribute_gap → vector_node → hydration_node → ...
           └─ action=EXPLAIN      → explain_node → 종단 체인

    secondary_intent 팬아웃(enable_secondary_intent=True):
      cache_check miss → [sql_node, vector_node] 병렬 → rrf_fusion_node → hydration_node
    """
    builder: StateGraph = StateGraph(AgentState)

    # ── 노드 등록 (GraphNodes 바운드 메서드 직접 등록) ──
    builder.add_node("reference_resolution_node", nodes.reference_resolution_node)
    builder.add_node("rehydrate_node", nodes.rehydrate_node)
    builder.add_node("describe_node", nodes.describe_node)
    # 책임 분리: triage_node(action 결정) → router_node(검색 계획) → cache_check.
    builder.add_node("triage_node", nodes.triage_node)
    builder.add_node("router_node", nodes.router_node)
    builder.add_node("cache_check_node", nodes.cache_check_node)
    builder.add_node("cache_write_node", nodes.cache_write_node)
    builder.add_node("retry_prep_node", nodes.retry_prep_node)
    builder.add_node("sql_node", nodes.sql_node)
    builder.add_node("vector_node", nodes.vector_node)
    builder.add_node("map_node", nodes.map_node)
    builder.add_node("analytics_node", nodes.analytics_node)
    builder.add_node("hydration_node", nodes.hydration_node)
    builder.add_node("rrf_fusion_node", nodes.rrf_fusion_node)
    builder.add_node("pre_answer_gate_node", nodes.pre_answer_gate_node)
    builder.add_node("answer_node", nodes.answer_node)
    builder.add_node("search_persist_node", nodes.search_persist_node)
    builder.add_node("trace_node", nodes.trace_node)
    # [C] W2 action 노드
    builder.add_node("direct_answer_node", nodes.direct_answer_node)
    builder.add_node("ambiguous_node", nodes.ambiguous_node)
    builder.add_node("out_of_scope_node", nodes.out_of_scope_node)
    builder.add_node("explain_node", nodes.explain_node)

    # ── START → reference_resolution ──
    builder.add_edge(START, "reference_resolution_node")
    builder.add_conditional_edges(
        "reference_resolution_node",
        nodes.route_after_reference,
        {
            "rehydrate_node": "rehydrate_node",
            "triage_node": "triage_node",
        },
    )

    # ── W1 참조 해소 경로 ──
    builder.add_edge("rehydrate_node", "describe_node")
    builder.add_edge("describe_node", "search_persist_node")

    # ── triage_node → route_by_action ──
    # RETRIEVE 는 router_node(검색 계획)로, 나머지 4종 action 은 각 종단 노드로.
    builder.add_conditional_edges(
        "triage_node",
        nodes.route_by_action,
        {
            "router_node": "router_node",
            "direct_answer_node": "direct_answer_node",
            "ambiguous_node": "ambiguous_node",
            "out_of_scope_node": "out_of_scope_node",
            "explain_node": "explain_node",
            "answer_node": "answer_node",
        },
    )

    # ── router_node(검색 계획) → cache_check ──
    builder.add_edge("router_node", "cache_check_node")

    # ── cache_check → fanout or intent 분기 ──
    builder.add_conditional_edges(
        "cache_check_node",
        nodes.post_cache_check,
        {
            "search_persist_node": "search_persist_node",
            "sql_node": "sql_node",
            "vector_node": "vector_node",
            "map_node": "map_node",
            "analytics_node": "analytics_node",
            "answer_node": "answer_node",
        },
    )

    # ── out_of_scope_node: attribute_gap → vector_node, domain_outside → END 체인 ──
    # attribute_gap은 out_of_scope_node 내부에서 intent=VECTOR_SEARCH +
    # vector_sub_intent=identification 세팅 후 일반 검색 경로로 연결된다.
    # domain_outside는 answer가 이미 세팅되므로 search_persist → trace 종단 체인.
    builder.add_conditional_edges(
        "out_of_scope_node",
        _out_of_scope_route,
        {
            "vector_node": "vector_node",
            "search_persist_node": "search_persist_node",
        },
    )

    # ── sql / vector → hydration → rrf_fusion → pre_answer_gate ──
    builder.add_edge("sql_node", "hydration_node")
    builder.add_edge("vector_node", "hydration_node")
    builder.add_edge("hydration_node", "rrf_fusion_node")
    builder.add_edge("rrf_fusion_node", "pre_answer_gate_node")

    # C2 게이트: 0건 → retry_prep_node, 유건 → answer_node
    builder.add_conditional_edges(
        "pre_answer_gate_node",
        nodes.route_pre_answer_gate,
        {
            "answer_node": "answer_node",
            "retry_prep_node": "retry_prep_node",
        },
    )

    # map_node / analytics_node는 hydration 없이 answer_node 직행
    builder.add_edge("map_node", "answer_node")
    builder.add_edge("analytics_node", "answer_node")

    # ── answer_node → self_correction or 종단 체인 ──
    builder.add_conditional_edges(
        "answer_node",
        nodes.self_correction_edge,
        {
            "end_normal": "cache_write_node",
            "retry_prep_node": "retry_prep_node",
        },
    )

    # ── 재시도 준비 → router_node 재진입 ──
    # self-correction 은 RETRIEVE 경로 전용이고(비-RETRIEVE 제외), 방향성 재시도
    # (SQL→VECTOR 전환·MAP 반경 확장)는 검색 *계획* 재수립이므로 Router 의 책임이다.
    # action 은 이미 RETRIEVE 로 확정됐으므로 triage 를 다시 거치지 않는다.
    builder.add_edge("retry_prep_node", "router_node")

    # ── 비-RETRIEVE action 종단 체인 ──
    # direct_answer / ambiguous / explain → 검색 없이 종단 체인
    for _non_retrieve_node in ("direct_answer_node", "ambiguous_node", "explain_node"):
        builder.add_edge(_non_retrieve_node, "cache_write_node")

    # ── 종단 체인 ──
    builder.add_edge("cache_write_node", "search_persist_node")
    builder.add_edge("search_persist_node", "trace_node")
    builder.add_edge("trace_node", END)

    return builder.compile()


_StreamEvent = (
    tuple[Literal["progress"], dict[str, str]]
    | tuple[Literal["decision"], dict[str, Any]]
    | tuple[Literal["result"], AgentState]
)


def _prepare_state(state: AgentState) -> AgentState:
    """run()/stream() 진입 시 per-request 런타임 상태를 state 에 초기화한다.

    제안 0: GraphNodes.prepare()(인스턴스 속성에 세션/경로/시작시각 주입)를 대체한다.
    node_path 는 reducer 가 누적하므로 빈 리스트로, started_at 은 elapsed_ms 산출용
    시작 시각으로 세팅한다. retry_count 는 기존과 동일하게 미존재 시 0으로 채운다.
    """
    overrides: dict[str, Any] = {}
    # routers/chat.py 가 항상 retry_count=0 으로 채워 넘기므로 이 분기는
    # 정상 요청 경로에서는 실행되지 않는다. 테스트에서 부분 dict(retry_count 미포함)를
    # 직접 넘길 때를 위한 방어 코드다.
    if "retry_count" not in state:
        overrides["retry_count"] = 0
    overrides["started_at"] = time.monotonic()
    overrides["node_path"] = []
    return {**state, **overrides}  # type: ignore[return-value]


class AgentGraph:
    """LangGraph StateGraph 기반 멀티에이전트 워크플로우.

    그래프 조립과 실행 인터페이스만 담당한다. 노드·엣지 구현은 GraphNodes에 위임한다.

        run(state) → AgentState
        stream(state) → AsyncGenerator[_StreamEvent]

    제안 0-6: DB 노드가 세션을 노드 내부에서 acquire-use-release 하므로 run()/stream()
    은 더 이상 세션을 주입받지 않는다.

    CompiledGraph는 인스턴스 단위로 컴파일된다(__init__에서 1회). 컴파일 비용이
    저렴하므로 클래스 수준 캐시 없이도 오버헤드가 무시할 수준이다.
    """

    def __init__(
        self,
        router: RouterAgent | TriageAgent | None = None,
        sql_agent: SqlAgent | None = None,
        vector_agent: VectorAgent | None = None,
        answer_agent: AnswerAgent | None = None,
        analytics_agent: AnalyticsAgent | None = None,
        redis: Any = None,
        triage: TriageAgent | None = None,
    ) -> None:
        # 책임 분리: TriageAgent(action 결정) + RouterAgent(검색 계획) 둘 다 노드에서 쓰인다.
        #   - triage: action 결정 노드(triage_node)
        #   - router: 검색 계획 노드(router_node, RETRIEVE 경로에서만 실행)
        # `router` 인자가 TriageAgent 인스턴스이면 하위호환으로 triage 로 받아들인다.
        _triage = triage or (router if isinstance(router, TriageAgent) else None)
        _router = router if isinstance(router, RouterAgent) else None
        # 하위호환: RouterAgent 만 명시 주입(triage 미주입)된 경우 triage 를 기본 생성하지
        # 않는다 — triage_node 가 RouterAgent fallback 분기로 action 을 결정한다.
        if _triage is None and _router is None:
            _triage = TriageAgent()
        if _triage is not None and _router is None:
            _router = RouterAgent()
        self._nodes = GraphNodes(
            router=_router,
            sql_agent=sql_agent or SqlAgent(),
            vector_agent=vector_agent or VectorAgent(),
            answer_agent=answer_agent or AnswerAgent(),
            analytics_agent=analytics_agent or AnalyticsAgent(),
            redis=redis,
            triage=_triage,
        )

        # 그래프는 인스턴스 단위로 1회 컴파일한다(바운드 메서드 직접 등록).
        self._compiled_graph = _build_graph(self._nodes)

    # ---------------------------------------------------------------------------
    # 공개 인터페이스
    # ---------------------------------------------------------------------------

    async def run(self, state: AgentState) -> AgentState:
        """그래프 전체 실행.

        Returns:
            answer, intent, trace, retry_count가 채워진 AgentState
        """
        state = _prepare_state(state)

        # recursion_limit=28 (Triage/Router 책임 분리로 RETRIEVE 경로에 router 단계 +1):
        # 1회 정상 흐름(최악 경로, RETRIEVE + secondary_intent 팬아웃):
        #   reference_resolution(1) → triage(2) → router(3) → cache_check(4) →
        #   sql_node+vector_node 병렬 팬아웃(5, 두 노드는 동일 super-step) →
        #   hydration(6) → rrf_fusion(7) → pre_answer_gate(8) → answer(9) →
        #   cache_write(10) → search_persist(11) → trace(12) = 12 super-step.
        #   (병렬 팬아웃은 한 super-step 에 묶이므로 노드 수가 늘어도 step 은 +1만.)
        # retry 1회 포함 시 retry_prep(+1) → router/cache_check/search/hydration/
        #   rrf_fusion/pre_answer_gate/answer/cache_write/search_persist/trace
        #   재실행(+10) = 합계 23 super-step (재시도는 triage 미경유 — router 재진입).
        # 참조 해소 경로는 더 짧다(reference → rehydrate → describe →
        #   search_persist → trace = 5). 여유 5를 더해 28로 설정한다.
        # 세션은 노드 내부에서 acquire-use-release(제안 0-6: 노드 로컬 세션).
        result: AgentState = await self._compiled_graph.ainvoke(
            state,
            config={"recursion_limit": 28},
        )  # type: ignore[arg-type]

        return result

    async def stream(
        self,
        state: AgentState,
    ) -> AsyncGenerator[_StreamEvent, None]:
        """그래프를 실행하며 진행 이벤트와 최종 결과를 yield한다.

        작업 3: 노드가 get_stream_writer 로 자기 progress/decision 이벤트를 직접
        emit 한다(agents/_helpers.py). stream() 은 더 이상 "어느 단계인지" 를
        node_name 으로 역추론하지 않는다 — "custom" 청크의 `_evt` 타입으로만 분기해
        그대로 SSE 튜플로 변환한다(가드 플래그·보류 변수 일체 제거).

        Yields:
            ("progress", {"step": str, "message": str}) — 각 단계 전환 시점
            ("decision", DecisionEvent dict)            — W3 판단 근거 (조건부)
            ("result", AgentState)                      — 최종 완료 상태

        emit 위치(노드 측, agents/nodes.py)와 타이밍:
            graph 시작 전(여기)  → routing  (노드 진입 전이라 writer 못 씀)
            triage_node          → 비-RETRIEVE면 decision(routes=[]) + answering
            router_node          → RETRIEVE면 decision(routes) + searching/answering
            rehydrate_node       → answering (W1 참조 해소 경로)
            retry_prep_node      → re_searching (+ progress 가드 리셋)
            search node          → answering

        decision 은 전체 실행 1회(노드의 decision_emitted 슬롯 가드), progress 의
        searching/answering 은 단계별 1회(searching/answering_emitted 슬롯,
        retry_prep_node 가 리셋해 재검색 시 다시 흐름).
        """
        state = _prepare_state(state)

        # 그래프 시작 전: routing 단계 진입 알림 (노드 진입 전이라 writer 사용 불가).
        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}

        # 최종 결과는 LangGraph가 reducer를 적용한 "values" 스냅샷을 사용한다.
        # (수동 합산은 node_path/search_channels reducer를 우회해 정합성이 깨짐.)
        # 가장 최근 "values" 청크를 보관했다가 루프 종료 후 yield.
        last_values: dict[str, Any] = dict(state)

        # 멀티모드: "values"(reducer 적용 전체 state)로 최종 result 스냅샷,
        # "custom"(노드가 writer 로 보낸 progress/decision 페이로드)으로 SSE 이벤트.
        # "updates" 는 더 이상 progress/decision 산출에 쓰이지 않으므로 받지 않는다.
        async for mode, chunk in self._compiled_graph.astream(
            state,
            stream_mode=["values", "custom"],
            config={"recursion_limit": 28},  # 최악 경로 23 super-step + 여유 5
        ):
            if mode == "values":
                # reducer가 적용된 전체 state 스냅샷.
                last_values = chunk
                continue

            # mode == "custom": 노드가 get_stream_writer 로 보낸 페이로드.
            evt = chunk.get("_evt")
            if evt == "progress":
                yield "progress", {"step": chunk["step"], "message": chunk["message"]}
            elif evt == "decision":
                yield (
                    "decision",
                    DecisionEvent(
                        action=chunk["action"],
                        routes=chunk["routes"],
                        user_rationale=chunk["user_rationale"],
                    ).model_dump(),
                )

        yield "result", last_values  # type: ignore[misc]
