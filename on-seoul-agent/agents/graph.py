"""LangGraph StateGraph 기반 멀티에이전트 워크플로우 (Phase 17).

그래프 구조:
    START
      ↓
    router_node          — RouterAgent.classify(), state.intent 설정
      ↓
    (conditional edge)   — intent에 따라 분기
      ├─ SQL_SEARCH    → sql_node
      ├─ VECTOR_SEARCH → vector_node
      ├─ MAP           → map_node
      └─ FALLBACK      → answer_node (검색 없이 바로 답변)
      ↓
    answer_node          — AnswerAgent.answer()
      ↓
    (self_correction)    — 빈 답변일 때만 재시도 + retry_count==0 → retry_prep_node 경유
      ↓ (정상) 또는 사이클
    [retry_prep_node]    — retry_count 증가 + 이전 검색 결과 초기화 → router_node 재진입
    trace_node           — chat_agent_traces 저장 (best-effort, 종단 노드)
      ↓
    END

책임 분리:
    노드·엣지 구현  → agents/nodes.py (GraphNodes)
    그래프 조립·실행 → 이 파일 (AgentGraph)

메모리 설계:
    CompiledGraph는 AgentGraph._compiled_graph에 클래스 수준으로 캐시된다.
    노드 함수는 contextvars.ContextVar(_ACTIVE_NODES)로 현재 GraphNodes 인스턴스를
    조회하는 모듈 수준 함수이므로, CompiledGraph → AgentGraph 역참조(순환 참조)가
    발생하지 않는다.

세션 주입:
    data_session : on_data DB (SQL 검색 — SqlAgent)
    ai_session   : on_ai DB  (Vector 검색 + trace 저장 — VectorAgent, trace_node)
"""

import contextvars
import logging
from collections.abc import AsyncGenerator
from typing import Any, ClassVar, Literal

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from agents.answer_agent import AnswerAgent
from agents.nodes import GraphNodes
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from schemas.state import AgentState, IntentType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context variable — run()/stream() 실행 중 현재 GraphNodes 인스턴스를 보유한다.
# 모듈 수준 dispatch 함수들이 이것을 통해 노드 메서드를 호출한다.
# CompiledGraph → AgentGraph 역참조를 만들지 않기 위한 핵심 설계.
# ---------------------------------------------------------------------------

_ACTIVE_NODES: contextvars.ContextVar[GraphNodes] = contextvars.ContextVar(
    "_active_nodes"
)

# ---------------------------------------------------------------------------
# 모듈 수준 dispatch 함수 — CompiledGraph에 등록된다.
# self를 직접 클로저로 캡처하지 않으므로 AgentGraph와의 순환 참조가 없다.
# ---------------------------------------------------------------------------


async def _dispatch_router_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().router_node(state)


async def _dispatch_retry_prep_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().retry_prep_node(state)


async def _dispatch_sql_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().sql_node(state)


async def _dispatch_vector_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().vector_node(state)


async def _dispatch_map_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().map_node(state)


async def _dispatch_answer_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().answer_node(state)


async def _dispatch_trace_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_NODES.get().trace_node(state)


def _dispatch_route_by_intent(state: AgentState) -> str:
    return _ACTIVE_NODES.get().route_by_intent(state)


def _dispatch_self_correction_edge(state: AgentState) -> str:
    return _ACTIVE_NODES.get().self_correction_edge(state)


# ---------------------------------------------------------------------------
# 공유 그래프 빌드 (프로세스당 1회)
# ---------------------------------------------------------------------------


def _build_shared_graph() -> Any:
    """StateGraph를 구성하고 컴파일한다. dispatch 함수만 사용하므로 재사용 가능."""
    builder: StateGraph = StateGraph(AgentState)

    builder.add_node("router_node", _dispatch_router_node)
    builder.add_node("retry_prep_node", _dispatch_retry_prep_node)
    builder.add_node("sql_node", _dispatch_sql_node)
    builder.add_node("vector_node", _dispatch_vector_node)
    builder.add_node("map_node", _dispatch_map_node)
    builder.add_node("answer_node", _dispatch_answer_node)
    builder.add_node("trace_node", _dispatch_trace_node)

    builder.add_edge(START, "router_node")

    builder.add_conditional_edges(
        "router_node",
        _dispatch_route_by_intent,
        {
            "sql_node": "sql_node",
            "vector_node": "vector_node",
            "map_node": "map_node",
            "answer_node": "answer_node",
        },
    )

    builder.add_edge("sql_node", "answer_node")
    builder.add_edge("vector_node", "answer_node")
    builder.add_edge("map_node", "answer_node")

    builder.add_conditional_edges(
        "answer_node",
        _dispatch_self_correction_edge,
        {
            "trace_node": "trace_node",
            "retry_prep_node": "retry_prep_node",
        },
    )

    # 재시도 준비 완료 후 router_node로 재진입
    builder.add_edge("retry_prep_node", "router_node")
    builder.add_edge("trace_node", END)

    return builder.compile()


_StreamEvent = (
    tuple[Literal["progress"], dict[str, str]]
    | tuple[Literal["result"], AgentState]
)


class AgentGraph:
    """LangGraph StateGraph 기반 멀티에이전트 워크플로우.

    그래프 조립과 실행 인터페이스만 담당한다. 노드·엣지 구현은 GraphNodes에 위임한다.

        run(state, *, data_session, ai_session) → AgentState
        stream(state, *, data_session, ai_session) → AsyncGenerator[_StreamEvent]

    CompiledGraph는 클래스 수준 캐시(_compiled_graph)에 저장되어 프로세스 내에서
    단 1회만 컴파일된다. 각 인스턴스는 캐시를 재사용하므로 메모리 오버헤드가 없다.
    """

    _compiled_graph: ClassVar[Any] = None

    def __init__(
        self,
        router: RouterAgent | None = None,
        sql_agent: SqlAgent | None = None,
        vector_agent: VectorAgent | None = None,
        answer_agent: AnswerAgent | None = None,
    ) -> None:
        self._nodes = GraphNodes(
            router=router or RouterAgent(),
            sql_agent=sql_agent or SqlAgent(),
            vector_agent=vector_agent or VectorAgent(),
            answer_agent=answer_agent or AnswerAgent(),
        )

        # 그래프는 클래스 수준에서 한 번만 컴파일한다.
        if AgentGraph._compiled_graph is None:
            AgentGraph._compiled_graph = _build_shared_graph()

    # ---------------------------------------------------------------------------
    # 공개 인터페이스
    # ---------------------------------------------------------------------------

    async def run(
        self,
        state: AgentState,
        *,
        data_session: AsyncSession,
        ai_session: AsyncSession,
    ) -> AgentState:
        """그래프 전체 실행.

        Returns:
            answer, intent, trace, retry_count가 채워진 AgentState
        """
        self._nodes.prepare(data_session, ai_session)

        if "retry_count" not in state:
            state = {**state, "retry_count": 0}

        token = _ACTIVE_NODES.set(self._nodes)
        try:
            result: AgentState = await AgentGraph._compiled_graph.ainvoke(
                state,
                config={"recursion_limit": 10},
            )  # type: ignore[arg-type]
        finally:
            _ACTIVE_NODES.reset(token)

        return result

    async def stream(
        self,
        state: AgentState,
        *,
        data_session: AsyncSession,
        ai_session: AsyncSession,
    ) -> AsyncGenerator[_StreamEvent, None]:
        """그래프를 실행하며 진행 이벤트와 최종 결과를 yield한다.

        LangGraph astream()으로 노드 완료 시점마다 진행 이벤트를 emit한다.
        각 progress 이벤트는 "방금 완료된 노드" 기준으로 "다음 단계"를 안내한다.

        Yields:
            ("progress", {"step": str, "message": str}) — 각 단계 전환 시점
            ("result", AgentState)                      — 최종 완료 상태

        진행 이벤트 타이밍:
            graph 시작 전        → routing  "질문을 분석하고 있습니다..."
            router_node 완료 후  → searching "관련 정보를 검색하고 있습니다..."
                                   (FALLBACK/error 시 → answering 즉시)
            search node 완료 후  → answering "답변을 생성하고 있습니다..."
        """
        self._nodes.prepare(data_session, ai_session)

        if "retry_count" not in state:
            state = {**state, "retry_count": 0}

        # 그래프 시작 전: routing 단계 진입 알림
        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}

        # state 누적 — astream()은 노드별 업데이트만 반환하므로 직접 합산
        accumulated: dict[str, Any] = dict(state)
        # 중복 emit 방지 (self-correction 루프에서 노드가 재실행될 수 있음)
        _search_progress_emitted = False
        _answer_progress_emitted = False

        _SEARCH_NODES = frozenset({"sql_node", "vector_node", "map_node"})

        token = _ACTIVE_NODES.set(self._nodes)
        try:
            async for chunk in AgentGraph._compiled_graph.astream(
                state,
                config={"recursion_limit": 10},
            ):
                node_name: str = next(iter(chunk))
                node_updates: dict[str, Any] = chunk[node_name]
                accumulated.update(node_updates)

                if node_name == "router_node" and not _search_progress_emitted:
                    _search_progress_emitted = True
                    intent = accumulated.get("intent")
                    # FALLBACK이거나 router_node 에러 시 검색 없이 바로 답변 단계로 간다.
                    if intent in (IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH, IntentType.MAP):
                        yield "progress", {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."}
                    else:
                        _answer_progress_emitted = True
                        yield "progress", {"step": "answering", "message": "답변을 생성하고 있습니다..."}

                elif node_name in _SEARCH_NODES and not _answer_progress_emitted:
                    _answer_progress_emitted = True
                    yield "progress", {"step": "answering", "message": "답변을 생성하고 있습니다..."}
        finally:
            _ACTIVE_NODES.reset(token)

        yield "result", accumulated  # type: ignore[misc]
