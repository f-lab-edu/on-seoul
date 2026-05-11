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
    (self_correction)    — 빈 답변일 때만 재시도 + retry_count==0 → router_node 재진입
      ↓ (정상) 또는 사이클
    trace_node           — chat_agent_traces 저장 (best-effort, 종단 노드)
      ↓
    END

자기 교정(Self-Correction):
    answer_node 완료 후 answer.strip()이 비어 있고 retry_count == 0인 경우에만
    router_node로 돌아가 재검색을 시도한다.
    retry_count >= 1이면 trace_node로 진행하여 무한 루프를 방지한다.

    router_node 재진입 시 retry_count를 1로 올려 무한 루프를 방지한다.
    (_node_path에 이미 "router"가 있으면 재진입으로 판단)

메모리 설계:
    CompiledGraph는 AgentGraph._compiled_graph에 클래스 수준으로 캐시된다.
    노드 함수는 contextvars.ContextVar(_ACTIVE_GRAPH)로 현재 인스턴스를 조회하는
    모듈 수준 함수이므로, CompiledGraph → AgentGraph 역참조(순환 참조)가 발생하지 않는다.
    AgentGraph 인스턴스는 테스트 종료 후 즉시 GC될 수 있다.

세션 주입:
    data_session : on_data DB (SQL 검색 — SqlAgent)
    ai_session   : on_ai DB  (Vector 검색 + trace 저장 — VectorAgent, trace_node)
"""

import contextvars
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, ClassVar, Literal

from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.answer_agent import AnswerAgent
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from schemas.state import AgentState, IntentType
from tools.map_search import map_search

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Context variable — run()/stream() 실행 중 현재 AgentGraph 인스턴스를 보유한다.
# 모듈 수준 dispatch 함수들이 이것을 통해 인스턴스 메서드를 호출한다.
# CompiledGraph → AgentGraph 역참조를 만들지 않기 위한 핵심 설계.
# ---------------------------------------------------------------------------

_ACTIVE_GRAPH: contextvars.ContextVar["AgentGraph"] = contextvars.ContextVar(
    "_active_graph"
)

# ---------------------------------------------------------------------------
# 모듈 수준 dispatch 함수 — CompiledGraph에 등록된다.
# self를 직접 클로저로 캡처하지 않으므로 AgentGraph와의 순환 참조가 없다.
# ---------------------------------------------------------------------------


async def _dispatch_router_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._router_node(state)


async def _dispatch_sql_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._sql_node(state)


async def _dispatch_vector_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._vector_node(state)


async def _dispatch_map_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._map_node(state)


async def _dispatch_answer_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._answer_node(state)


async def _dispatch_trace_node(state: AgentState) -> dict[str, Any]:
    return await _ACTIVE_GRAPH.get()._trace_node(state)


def _dispatch_route_by_intent(state: AgentState) -> str:
    return _ACTIVE_GRAPH.get()._route_by_intent(state)


def _dispatch_self_correction_edge(state: AgentState) -> str:
    return _ACTIVE_GRAPH.get()._self_correction_edge(state)


# ---------------------------------------------------------------------------
# 공유 그래프 빌드 (프로세스당 1회)
# ---------------------------------------------------------------------------


def _build_shared_graph() -> Any:
    """StateGraph를 구성하고 컴파일한다. dispatch 함수만 사용하므로 재사용 가능."""
    builder: StateGraph = StateGraph(AgentState)

    builder.add_node("router_node", _dispatch_router_node)
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
            "router_node": "router_node",
        },
    )

    builder.add_edge("trace_node", END)

    return builder.compile()


_StreamEvent = (
    tuple[Literal["progress"], dict[str, str]]
    | tuple[Literal["result"], AgentState]
)


class AgentGraph:
    """LangGraph StateGraph 기반 멀티에이전트 워크플로우.

    AgentWorkflow와 동일한 인터페이스를 제공한다:
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
        self._router = router or RouterAgent()
        self._sql = sql_agent or SqlAgent()
        self._vector = vector_agent or VectorAgent()
        self._answer = answer_agent or AnswerAgent()

        # 런타임 세션 — run()/stream() 진입 시 설정된다.
        self._data_session: AsyncSession | None = None
        self._ai_session: AsyncSession | None = None
        # 실행 시작 시각 (elapsed_ms 계산)
        self._start: float = 0.0
        # 노드 실행 경로 기록
        self._node_path: list[str] = []

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
        self._data_session = data_session
        self._ai_session = ai_session
        self._start = time.monotonic()
        self._node_path = []

        if "retry_count" not in state:
            state = {**state, "retry_count": 0}

        token = _ACTIVE_GRAPH.set(self)
        try:
            result: AgentState = await AgentGraph._compiled_graph.ainvoke(
                state,
                config={"recursion_limit": 10},
            )  # type: ignore[arg-type]
        finally:
            _ACTIVE_GRAPH.reset(token)

        return result

    async def stream(
        self,
        state: AgentState,
        *,
        data_session: AsyncSession,
        ai_session: AsyncSession,
    ) -> AsyncGenerator[_StreamEvent, None]:
        """그래프를 실행하며 진행 이벤트와 최종 결과를 yield한다.

        Yields:
            ("progress", {"step": str, "message": str}) — 각 단계 시작 전
            ("result", AgentState)                      — 최종 완료 상태
        """
        self._data_session = data_session
        self._ai_session = ai_session
        self._start = time.monotonic()
        self._node_path = []

        if "retry_count" not in state:
            state = {**state, "retry_count": 0}

        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
        yield "progress", {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."}
        yield "progress", {"step": "answering", "message": "답변을 생성하고 있습니다..."}

        token = _ACTIVE_GRAPH.set(self)
        try:
            result: AgentState = await AgentGraph._compiled_graph.ainvoke(
                state,
                config={"recursion_limit": 10},
            )  # type: ignore[arg-type]
        finally:
            _ACTIVE_GRAPH.reset(token)

        yield "result", result

    # ---------------------------------------------------------------------------
    # 노드 구현 (인스턴스 메서드 — _ACTIVE_GRAPH를 통해 dispatch 함수에서 호출)
    # ---------------------------------------------------------------------------

    async def _router_node(self, state: AgentState) -> dict[str, Any]:
        """RouterAgent.classify() 호출 — intent 설정.

        재진입 감지: _node_path에 이미 "router"가 있으면 자기 교정 재시도이므로
        retry_count를 1로 올리고 이전 error를 클리어한다.
        """
        is_retry = "router" in self._node_path
        retry_count = 1 if is_retry else state.get("retry_count", 0)

        try:
            new_state = await self._router.classify(state)
            self._node_path.append("router")
            updates: dict[str, Any] = {"intent": new_state["intent"], "retry_count": retry_count}
            if is_retry:
                updates["error"] = None
            return updates
        except Exception as exc:
            logger.exception("router_node 실행 오류")
            self._node_path.append("router_error")
            fallback_answer = "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
            return {
                "error": str(exc),
                "answer": fallback_answer,
                "retry_count": retry_count,
            }

    async def _sql_node(self, state: AgentState) -> dict[str, Any]:
        """SqlAgent.search() 호출 — sql_results 설정."""
        assert self._data_session is not None
        try:
            new_state = await self._sql.search(state, self._data_session)
            self._node_path.append("sql_node")
            return {"sql_results": new_state.get("sql_results")}
        except Exception as exc:
            logger.exception("sql_node 실행 오류")
            self._node_path.append("sql_error")
            return {"error": str(exc)}

    async def _vector_node(self, state: AgentState) -> dict[str, Any]:
        """VectorAgent.search() 호출 — vector_results, refined_query 설정."""
        assert self._ai_session is not None
        try:
            new_state = await self._vector.search(state, self._ai_session)
            self._node_path.append("vector_node")
            return {
                "vector_results": new_state.get("vector_results"),
                "refined_query": new_state.get("refined_query"),
            }
        except Exception as exc:
            logger.exception("vector_node 실행 오류")
            self._node_path.append("vector_error")
            return {"error": str(exc)}

    async def _map_node(self, state: AgentState) -> dict[str, Any]:
        """map_search 호출 — map_results 설정."""
        assert self._data_session is not None
        lat = state.get("lat")
        lng = state.get("lng")
        if lat is not None and lng is not None:
            try:
                geojson = await map_search(self._data_session, lat, lng)
                self._node_path.append("map_node")
                return {"map_results": geojson}
            except Exception as exc:
                logger.exception("map_node 실행 오류")
                self._node_path.append("map_error")
                return {"error": str(exc)}
        else:
            logger.warning("map_node — lat/lng 미제공, map_results=None 처리")
            self._node_path.append("map_node")
            return {"map_results": None}

    async def _answer_node(self, state: AgentState) -> dict[str, Any]:
        """AnswerAgent.answer() 호출 — answer, title 설정."""
        if state.get("error") and state.get("answer"):
            self._node_path.append("answer_node")
            return {}

        try:
            new_state = await self._answer.answer(state)
            self._node_path.append("answer_node")
            return {
                "answer": new_state.get("answer"),
                "title": new_state.get("title"),
            }
        except Exception as exc:
            logger.exception("answer_node 실행 오류")
            self._node_path.append("answer_error")
            return {
                "error": str(exc),
                "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            }

    async def _trace_node(self, state: AgentState) -> dict[str, Any]:
        """chat_agent_traces 저장 (best-effort 종단 노드)."""
        assert self._ai_session is not None
        elapsed_ms = int((time.monotonic() - self._start) * 1000)
        trace_payload: dict[str, Any] = {
            "intent": state.get("intent"),
            "node_path": list(self._node_path),
            "elapsed_ms": elapsed_ms,
            "error": state.get("error"),
        }
        await _save_trace(self._ai_session, state["message_id"], trace_payload)
        return {"trace": trace_payload}

    # ---------------------------------------------------------------------------
    # 조건부 엣지 함수
    # ---------------------------------------------------------------------------

    def _route_by_intent(self, state: AgentState) -> str:
        """intent 값에 따라 다음 노드를 결정한다."""
        error = state.get("error")
        answer = state.get("answer") or ""

        # router_node 예외 시 fallback_answer + error가 모두 설정됨.
        # intent가 None이므로 아래 else 분기가 동일하게 처리하지만,
        # 의도를 명시하기 위한 early-return.
        if error and answer.strip():
            return "answer_node"

        intent = state.get("intent")
        if intent == IntentType.SQL_SEARCH:
            return "sql_node"
        elif intent == IntentType.VECTOR_SEARCH:
            return "vector_node"
        elif intent == IntentType.MAP:
            return "map_node"
        else:
            return "answer_node"

    def _self_correction_edge(self, state: AgentState) -> str:
        """answer_node 완료 후 자기 교정 여부를 결정한다.

        answer.strip()이 비어 있고 retry_count == 0인 경우에만 router_node로 복귀한다.
        answer가 있거나 retry_count >= 1이면 trace_node로 진행하여 무한 루프를 방지한다.
        """
        retry_count = state.get("retry_count", 0)
        answer = state.get("answer") or ""

        needs_retry = not answer.strip() and retry_count == 0

        if needs_retry:
            return "router_node"
        return "trace_node"


# ---------------------------------------------------------------------------
# Trace 저장 헬퍼
# ---------------------------------------------------------------------------


async def _save_trace(
    session: AsyncSession,
    message_id: int,
    trace: dict[str, Any],
) -> None:
    """chat_agent_traces 테이블에 실행 메타데이터를 저장한다."""
    try:
        trace_json = json.dumps(trace, ensure_ascii=False, default=str)
        await session.execute(
            text(
                "INSERT INTO chat_agent_traces (message_id, trace) "
                "VALUES (:message_id, CAST(:trace AS jsonb))"
            ),
            {"message_id": message_id, "trace": trace_json},
        )
        await session.commit()
    except Exception as exc:
        logger.warning("trace 저장 실패 (message_id=%s): %s", message_id, exc)
        try:
            await session.rollback()
        except Exception:
            pass
