"""AgentWorkflow 통합 테스트.

DB와 LLM을 모두 Mock으로 대체하여 라우팅 → 검색 → 답변 전체 흐름을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from agents.vector_agent import VectorAgent, _RefinedQuery
from agents.workflow import AgentWorkflow
from schemas.state import AgentState, IntentType


# ---------------------------------------------------------------------------
# 픽스처 헬퍼
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> AgentState:
    base = AgentState(
        room_id=1,
        message_id=10,
        message="수영장 알려줘",
        title_needed=False,
        intent=None,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )
    base.update(kwargs)
    return base


def _make_router(intent: IntentType) -> RouterAgent:
    agent = RouterAgent.__new__(RouterAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    agent._chain = chain
    return agent


def _make_sql_agent(rows: list[dict]) -> tuple[SqlAgent, MagicMock]:
    agent = SqlAgent.__new__(SqlAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_SqlParams())
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def _make_vector_agent(rows: list[dict]) -> tuple[VectorAgent, MagicMock]:
    agent = VectorAgent.__new__(VectorAgent)

    refine_chain = MagicMock()
    refine_chain.ainvoke = AsyncMock(return_value=_RefinedQuery(refined_query="정제된 질의"))
    agent._refine_chain = refine_chain

    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1, 0.2])
    agent._embeddings = embeddings

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def _make_answer_agent(answer: str = "답변입니다.", title: str | None = None) -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)

    answer_chain = MagicMock()
    answer_chain.ainvoke = AsyncMock(return_value=_AnswerOutput(answer=answer))
    agent._answer_chain = answer_chain

    title_chain = MagicMock()
    title_chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=title or "수영장 안내"))
    agent._title_chain = title_chain
    return agent


def _make_ai_session() -> MagicMock:
    """trace 저장용 on_ai DB Mock 세션."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# SQL_SEARCH 흐름
# ---------------------------------------------------------------------------


class TestSqlWorkflow:
    async def test_sql_search_end_to_end(self):
        """SQL_SEARCH: router → sql_agent → answer 전체 흐름 검증."""
        rows = [{"service_id": "S001", "service_name": "수영장", "service_url": None}]
        sql_agent, data_session = _make_sql_agent(rows)

        workflow = AgentWorkflow(
            router=_make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_make_answer_agent("강남 수영장 안내입니다."),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["intent"] == IntentType.SQL_SEARCH
        assert result["sql_results"] == rows
        assert result["answer"] == "강남 수영장 안내입니다."
        assert result["error"] is None

    async def test_sql_search_trace_fields(self):
        """SQL_SEARCH 실행 후 trace에 intent, node_path, elapsed_ms가 채워진다."""
        _, data_session = _make_sql_agent([])
        workflow = AgentWorkflow(
            router=_make_router(IntentType.SQL_SEARCH),
            sql_agent=_make_sql_agent([])[0],
            answer_agent=_make_answer_agent(),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        trace = result["trace"]
        assert trace["intent"] == IntentType.SQL_SEARCH
        assert "router" in trace["node_path"]
        assert "sql_agent" in trace["node_path"]
        assert "answer" in trace["node_path"]
        assert isinstance(trace["elapsed_ms"], int)
        assert trace["elapsed_ms"] >= 0

    async def test_title_generated_for_first_message(self):
        """title_needed=True이면 title이 state에 채워진다."""
        _, data_session = _make_sql_agent([])
        answer_agent = _make_answer_agent(title="수영장 조회")

        workflow = AgentWorkflow(
            router=_make_router(IntentType.SQL_SEARCH),
            sql_agent=_make_sql_agent([])[0],
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _make_state(title_needed=True),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["title"] == "수영장 조회"
        answer_agent._title_chain.ainvoke.assert_called_once()


# ---------------------------------------------------------------------------
# VECTOR_SEARCH 흐름
# ---------------------------------------------------------------------------


class TestVectorWorkflow:
    async def test_vector_search_end_to_end(self):
        """VECTOR_SEARCH: router → vector_agent → answer 전체 흐름 검증."""
        rows = [{"service_id": "V001", "service_name": "체험관", "similarity": 0.9}]
        vector_agent, data_session = _make_vector_agent(rows)

        workflow = AgentWorkflow(
            router=_make_router(IntentType.VECTOR_SEARCH),
            vector_agent=vector_agent,
            answer_agent=_make_answer_agent("체험관 안내입니다."),
        )
        result = await workflow.run(
            _make_state(message="아이랑 체험할 수 있는 곳"),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["intent"] == IntentType.VECTOR_SEARCH
        assert result["vector_results"] == rows
        assert result["answer"] == "체험관 안내입니다."

    async def test_vector_search_trace_includes_vector_node(self):
        """VECTOR_SEARCH trace의 node_path에 vector_agent가 포함된다."""
        vector_agent, data_session = _make_vector_agent([])

        workflow = AgentWorkflow(
            router=_make_router(IntentType.VECTOR_SEARCH),
            vector_agent=vector_agent,
            answer_agent=_make_answer_agent(),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert "vector_agent" in result["trace"]["node_path"]


# ---------------------------------------------------------------------------
# FALLBACK 흐름
# ---------------------------------------------------------------------------


class TestFallbackWorkflow:
    async def test_fallback_skips_search(self):
        """FALLBACK: 검색 없이 answer_agent만 호출된다."""
        sql_agent, data_session = _make_sql_agent([])
        vector_agent = _make_vector_agent([])[0]
        answer_agent = _make_answer_agent("서울시 공공서비스 예약 챗봇입니다.")

        workflow = AgentWorkflow(
            router=_make_router(IntentType.FALLBACK),
            sql_agent=sql_agent,
            vector_agent=vector_agent,
            answer_agent=answer_agent,
        )
        result = await workflow.run(
            _make_state(message="안녕하세요"),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["intent"] == IntentType.FALLBACK
        assert result["sql_results"] is None
        assert result["vector_results"] is None
        assert result["answer"] == "서울시 공공서비스 예약 챗봇입니다."
        sql_agent._chain.ainvoke.assert_not_called()
        vector_agent._refine_chain.ainvoke.assert_not_called()

    async def test_fallback_trace_node_path(self):
        """FALLBACK trace의 node_path: router → fallback → answer."""
        _, data_session = _make_sql_agent([])
        workflow = AgentWorkflow(
            router=_make_router(IntentType.FALLBACK),
            answer_agent=_make_answer_agent(),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["trace"]["node_path"] == ["router", "fallback", "answer"]


# ---------------------------------------------------------------------------
# MAP 흐름 (stub)
# ---------------------------------------------------------------------------


class TestMapWorkflow:
    async def test_map_intent_stub_proceeds_to_answer(self):
        """MAP intent는 map_search 미구현 상태에서 answer_agent로 진행한다."""
        _, data_session = _make_sql_agent([])
        workflow = AgentWorkflow(
            router=_make_router(IntentType.MAP),
            answer_agent=_make_answer_agent("지도 서비스는 준비 중입니다."),
        )
        result = await workflow.run(
            _make_state(message="내 주변 체육관 지도로 보여줘"),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["intent"] == IntentType.MAP
        assert result["answer"] == "지도 서비스는 준비 중입니다."
        assert "map_stub" in result["trace"]["node_path"]


# ---------------------------------------------------------------------------
# Trace 저장
# ---------------------------------------------------------------------------


class TestTraceStorage:
    async def test_trace_saved_to_ai_session(self):
        """워크플로우 실행 후 ai_session.execute가 INSERT로 호출된다."""
        _, data_session = _make_sql_agent([])
        ai_session = _make_ai_session()

        workflow = AgentWorkflow(
            router=_make_router(IntentType.FALLBACK),
            answer_agent=_make_answer_agent(),
        )
        await workflow.run(
            _make_state(message_id=42),
            data_session=data_session,
            ai_session=ai_session,
        )

        ai_session.execute.assert_called_once()
        call_args = ai_session.execute.call_args[0]
        sql_str = str(call_args[0])
        assert "chat_agent_traces" in sql_str
        bind = call_args[1]
        assert bind["message_id"] == 42

    async def test_trace_save_failure_does_not_raise(self):
        """trace 저장 실패 시 워크플로우 결과에 영향을 주지 않는다."""
        _, data_session = _make_sql_agent([])
        ai_session = _make_ai_session()
        ai_session.execute = AsyncMock(side_effect=Exception("DB 연결 끊김"))

        workflow = AgentWorkflow(
            router=_make_router(IntentType.FALLBACK),
            answer_agent=_make_answer_agent("답변"),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        # trace 저장 실패해도 answer는 정상 반환
        assert result["answer"] == "답변"

    async def test_state_preserved_across_workflow(self):
        """워크플로우는 room_id, message_id 등 초기 state 필드를 보존한다."""
        _, data_session = _make_sql_agent([])
        workflow = AgentWorkflow(
            router=_make_router(IntentType.FALLBACK),
            answer_agent=_make_answer_agent(),
        )
        result = await workflow.run(
            _make_state(room_id=99, message_id=77),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["room_id"] == 99
        assert result["message_id"] == 77


# ---------------------------------------------------------------------------
# 오류 처리
# ---------------------------------------------------------------------------


class TestWorkflowErrorHandling:
    async def test_error_state_set_on_agent_failure(self):
        """Agent 실행 중 예외 발생 시 state.error에 오류 메시지가 채워진다."""
        router = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("LLM 타임아웃"))
        router._chain = chain

        _, data_session = _make_sql_agent([])
        workflow = AgentWorkflow(
            router=router,
            answer_agent=_make_answer_agent(),
        )
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=_make_ai_session(),
        )

        assert result["error"] is not None
        assert "LLM 타임아웃" in result["error"]

    async def test_error_recorded_in_trace(self):
        """오류 발생 시 trace.node_path에 error가 포함된다."""
        router = RouterAgent.__new__(RouterAgent)
        chain = MagicMock()
        chain.ainvoke = AsyncMock(side_effect=RuntimeError("오류"))
        router._chain = chain

        _, data_session = _make_sql_agent([])
        ai_session = _make_ai_session()
        workflow = AgentWorkflow(router=router, answer_agent=_make_answer_agent())
        result = await workflow.run(
            _make_state(),
            data_session=data_session,
            ai_session=ai_session,
        )

        assert "error" in result["trace"]["node_path"]
        assert result["trace"]["error"] is not None
