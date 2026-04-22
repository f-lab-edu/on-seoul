"""LangChain LCEL 워크플로우 — Router → Branch → Answer + Trace 적재.

실행 흐름:
    사용자 메시지
      → RouterAgent (의도 분류)
      → 의도별 분기
          SQL_SEARCH    → SqlAgent → AnswerAgent
          VECTOR_SEARCH → VectorAgent → AnswerAgent
          MAP           → (stub: map_results 미구현) → AnswerAgent
          FALLBACK      → AnswerAgent (검색 생략)
      → chat_agent_traces 적재 (best-effort)
      → AgentState 반환

세션 주입:
    - data_session : on_data DB (SQL/Vector 검색용)
    - ai_session   : on_ai DB (trace 저장용)
    FastAPI 라우터에서 Depends로 주입하거나, 워크플로우 단독 실행 시
    core.database.ai_session_ctx / data_session_ctx 를 사용한다.

LangGraph 전환 시:
    AgentState 기반 입출력 규약을 유지하므로, workflow.py를 graph.py로
    교체하고 StateGraph 노드를 등록하면 된다. 각 Agent 메서드는 그대로 재사용.
"""

import json
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.answer_agent import AnswerAgent
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from schemas.state import AgentState, IntentType

logger = logging.getLogger(__name__)


class AgentWorkflow:
    """Router → Branch → Answer 순서로 실행하는 LCEL 워크플로우.

    각 Agent는 생성자에서 주입할 수 있어 단위 테스트 시 Mock으로 교체 가능하다.
    """

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

    async def run(
        self,
        state: AgentState,
        *,
        data_session: AsyncSession,
        ai_session: AsyncSession,
    ) -> AgentState:
        """워크플로우 전체 실행.

        Args:
            state: 초기 AgentState (room_id, message_id, message, title_needed 필수)
            data_session: on_data DB 세션 (SQL/Vector 검색)
            ai_session: on_ai DB 세션 (trace 저장)

        Returns:
            answer, intent, trace가 채워진 AgentState
        """
        start = time.monotonic()
        node_path: list[str] = []

        try:
            # ── 1. Router: 의도 분류 ──────────────────────────────────────
            state = await self._router.classify(state)
            node_path.append("router")
            intent: IntentType = state["intent"]

            # ── 2. Branch: 의도별 검색 ───────────────────────────────────
            state = await self._dispatch(state, intent, data_session, node_path)

            # ── 3. Answer: 자연어 답변 생성 ──────────────────────────────
            state = await self._answer.answer(state)
            node_path.append("answer")

        except Exception as exc:
            logger.exception("워크플로우 실행 중 오류: %s", exc)
            state = {**state, "error": str(exc)}
            node_path.append("error")

        finally:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            trace_payload: dict[str, Any] = {
                "intent": state.get("intent"),
                "node_path": node_path,
                "elapsed_ms": elapsed_ms,
                "error": state.get("error"),
            }
            state = {**state, "trace": trace_payload}

            # ── 4. Trace 적재 (best-effort) ───────────────────────────────
            await _save_trace(ai_session, state["message_id"], trace_payload)

        return state

    async def _dispatch(
        self,
        state: AgentState,
        intent: IntentType,
        session: AsyncSession,
        node_path: list[str],
    ) -> AgentState:
        """의도에 따라 적절한 Agent를 호출한다."""
        if intent == IntentType.SQL_SEARCH:
            state = await self._sql.search(state, session)
            node_path.append("sql_agent")

        elif intent == IntentType.VECTOR_SEARCH:
            state = await self._vector.search(state, session)
            node_path.append("vector_agent")

        elif intent == IntentType.MAP:
            # Phase 11에서 map_search 도구 연동 예정.
            # 현재는 검색 결과 없이 Answer Agent로 진행한다.
            logger.warning("MAP intent — map_search 미구현, FALLBACK으로 대체")
            node_path.append("map_stub")

        else:
            # FALLBACK — 검색 생략, Answer Agent에서 일반 안내 메시지 생성
            node_path.append("fallback")

        return state


# ---------------------------------------------------------------------------
# Trace 저장 헬퍼
# ---------------------------------------------------------------------------


async def _save_trace(
    session: AsyncSession,
    message_id: int,
    trace: dict[str, Any],
) -> None:
    """chat_agent_traces 테이블에 실행 메타데이터를 저장한다.

    저장 실패 시 로그만 남기고 워크플로우 결과에 영향을 주지 않는다.
    """
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
