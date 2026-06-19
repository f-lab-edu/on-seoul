"""제안 0-6 — 노드 로컬 세션(acquire-use-release) 회귀 테스트.

DB 를 쓰는 노드는 노드 내부에서 `data_session_ctx()`/`ai_session_ctx()` 로 세션을
잡고 즉시 반납한다. graph.run()/stream() 은 세션을 주입받지 않는다.

봉인 대상:
1. 세션이 노드 단위로 열고 닫힌다 — answer_node 실행 중 DB 커넥션 미점유
   (검색 단계에서 잡은 세션이 answer 진입 전 반납됨).
2. 노드 로컬 전환 후에도 요청 격리(0-1) 유지 — 동시 요청이 세션을 교차하지 않음.
3. search_persist·trace 가 독립 세션을 열고 멱등 적재(UNIQUE+ON CONFLICT)된다.
4. retry 경로에서 검색 노드 재진입 시 세션을 재획득한다.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.sql_agent import SqlAgent, _SqlParams
from agents.vector_agent import VectorAgent, _RefinedQuery
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_router,
    patch_node_sessions,
)


def _state(**kwargs):
    return make_agent_state(**kwargs)


class _RecordingSqlAgent(SqlAgent):
    """search() 가 받은 세션과 호출 시점을 기록한다."""

    def __init__(self, rows, *, on_search=None):
        self._chain = MagicMock()
        self._chain.ainvoke = AsyncMock(return_value=_SqlParams(keyword="kw"))
        self._rows = rows
        self._on_search = on_search
        self.seen_sessions: list[object] = []

    async def search(self, state, data_session):
        self.seen_sessions.append(data_session)
        if self._on_search is not None:
            await self._on_search()
        return {**state, "sql": {"results": self._rows, "keyword": "kw"}}


class _RecordingAnswerAgent:
    """answer() 호출 시점에 콜백을 실행해 '그 시점' 세션 점유를 관측한다."""

    def __init__(self, answer, *, on_answer=None):
        self._answer = answer
        self._on_answer = on_answer

    async def answer(self, state):
        if self._on_answer is not None:
            self._on_answer()
        return {**state, "answer": self._answer, "title": "T", "service_cards": []}


class TestSessionAcquireRelease:
    async def test_data_session_released_before_answer(self):
        """sql_node 가 잡은 data_session 은 answer_node 진입 전에 반납된다.

        ctx __aexit__ 호출 횟수로 반납을 확인한다 — answer 시점에 이미 닫혀 있어야 한다.
        """
        data_session = MagicMock(name="data")
        exits: list[str] = []

        # data_session_ctx 의 __aexit__ 를 추적하는 커스텀 ctx.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def tracking_data_ctx():
            try:
                yield data_session
            finally:
                exits.append("data")

        @asynccontextmanager
        async def ai_ctx():
            yield make_ai_session()

        observed_exits_at_answer: list[int] = []
        answer_agent = _RecordingAnswerAgent(
            "답변",
            on_answer=lambda: observed_exits_at_answer.append(len(exits)),
        )

        sql_agent = _RecordingSqlAgent([{"service_id": "S1"}])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=answer_agent,
        )

        with (
            patch("agents.nodes.data_session_ctx", tracking_data_ctx),
            patch("agents.nodes.ai_session_ctx", ai_ctx),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            result = await graph.run(_state())

        assert result["output"]["answer"] == "답변"
        # answer 시점에 data_session ctx 가 (sql + hydration) 적어도 한 번은 닫혀 있어야
        # 한다 = answer 가 data 커넥션을 점유하지 않음.
        assert observed_exits_at_answer and observed_exits_at_answer[0] >= 1

    async def test_run_takes_no_session_kwargs(self):
        """graph.run() 은 세션 인자를 받지 않는다(시그니처 회귀)."""
        sql_agent = _RecordingSqlAgent([{"service_id": "S1"}])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("답변"),
        )
        with (
            patch_node_sessions(data_session=MagicMock(), ai_session=make_ai_session()),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            result = await graph.run(_state())
        assert result["output"]["answer"] == "답변"


class TestConcurrentIsolationNodeLocal:
    async def test_two_concurrent_requests_do_not_cross_sessions(self):
        """동시 요청이 서로의 노드 로컬 세션을 잡지 않는다(0-1 격리 유지).

        프로덕션에서 두 요청은 같은 (실제) `data_session_ctx` 를 호출하지만, 매
        acquire 마다 풀에서 *별개* 세션을 받는다. 여기서는 ctx 를 호출마다 고유
        MagicMock 세션을 돌려주는 팩토리로 패치하고, barrier 로 두 요청을 sql search
        안에 동시에 머무르게 한 뒤, 각 요청의 search 가 받은 세션이 서로 겹치지
        않음을 단언한다. 과거 싱글톤 self.data_session 설계라면 여기서 한쪽이 다른
        쪽 세션을 덮어써 교차가 발생한다.
        """
        from contextlib import asynccontextmanager

        barrier = asyncio.Barrier(2)

        async def wait():
            await barrier.wait()

        sql_a = _RecordingSqlAgent([{"service_id": "S1"}], on_search=wait)
        sql_b = _RecordingSqlAgent([{"service_id": "S2"}], on_search=wait)

        # 호출마다 고유 세션을 돌려주는 공유 ctx — 풀 동작 모사(요청 구분 없이 단일 함수).
        counter = {"n": 0}

        @asynccontextmanager
        async def data_ctx():
            counter["n"] += 1
            yield MagicMock(name=f"data{counter['n']}")

        @asynccontextmanager
        async def ai_ctx():
            yield make_ai_session()

        graph_a = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_a,
            answer_agent=make_answer_agent("A"),
        )
        graph_b = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_b,
            answer_agent=make_answer_agent("B"),
        )

        with (
            patch("agents.nodes.data_session_ctx", data_ctx),
            patch("agents.nodes.ai_session_ctx", ai_ctx),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            res_a, res_b = await asyncio.gather(
                graph_a.run(_state(room_id=1, message_id=1)),
                graph_b.run(_state(room_id=2, message_id=2)),
            )

        # 두 요청의 sql search 세션 집합이 서로 겹치지 않는다 = 교차 없음.
        ids_a = set(map(id, sql_a.seen_sessions))
        ids_b = set(map(id, sql_b.seen_sessions))
        assert ids_a and ids_b
        assert ids_a.isdisjoint(ids_b)
        assert res_a["output"]["answer"] == "A"
        assert res_b["output"]["answer"] == "B"


class TestSearchPersistTraceIndependentSessions:
    async def test_persist_and_trace_use_independent_ai_sessions(self):
        """search_persist 와 trace 가 각자 독립 ai_session 을 연다.

        두 노드가 서로 다른 세션 객체를 받아야 한다(0-6: 트랜잭션 공유 없음).
        멱등 적재(ON CONFLICT)는 SQL 문에 보존되며, 두 세션 모두 commit 된다.
        """
        persist_session = make_ai_session()
        trace_session = make_ai_session()

        sql_agent = _RecordingSqlAgent([{"service_id": "S1"}])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("답변"),
        )

        with (
            # ai_session 은 두 번 acquire 된다: search_persist → trace.
            patch_node_sessions(
                data_session=MagicMock(),
                ai_sessions=(persist_session, trace_session),
            ),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            await graph.run(_state(message_id=99))

        # 두 노드가 서로 다른 세션을 받았다(독립).
        assert persist_session is not trace_session
        # search_persist 세션: chat_search_queries/results INSERT 후 commit.
        persist_session.commit.assert_awaited()
        # trace 세션: chat_agent_traces INSERT 후 commit.
        trace_session.commit.assert_awaited()
        # 두 세션 모두 execute 호출됨(서로 다른 테이블).
        assert persist_session.execute.await_count >= 1
        assert trace_session.execute.await_count >= 1

    async def test_persist_failure_does_not_corrupt_trace_session(self):
        """search_persist INSERT 실패가 trace 독립 세션을 오염시키지 않는다."""
        persist_session = make_ai_session()
        persist_session.execute = AsyncMock(side_effect=RuntimeError("insert fail"))
        trace_session = make_ai_session()

        sql_agent = _RecordingSqlAgent([{"service_id": "S1"}])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("답변"),
        )

        with (
            patch_node_sessions(
                data_session=MagicMock(),
                ai_sessions=(persist_session, trace_session),
            ),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            result = await graph.run(_state(message_id=100))

        # 워크플로우 결과는 정상(best-effort).
        assert result["output"]["answer"] == "답변"
        # trace 세션은 persist 실패와 무관하게 정상 commit.
        trace_session.commit.assert_awaited()


class TestSessionAcquireFailureBestEffort:
    async def test_trace_session_acquire_failure_does_not_break_workflow(self):
        """trace_node 의 ai_session_ctx() 획득 자체가 실패해도 워크플로우는 정상 종료.

        0-6 에서 trace_node 는 ai_session_ctx() 를 노드 내부에서 호출한다. 풀 고갈/
        커넥션 거부 등으로 *세션 획득* 단계가 예외를 던질 수 있다. 이 경로는 _save_trace
        의 내부 except 로는 잡히지 않으므로(아직 _save_trace 진입 전), trace_node 가
        별도 try/except 로 best-effort 보장해야 한다. answer 는 정상이어야 하고
        trace node_path 도 그대로 반환되어야 한다.
        """
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def good_data_ctx():
            yield MagicMock(name="data")

        acquire_calls = {"ai": 0}

        @asynccontextmanager
        async def failing_ai_ctx():
            # search_persist 는 정상 세션을 받고, trace 진입 시 획득 실패를 유발한다.
            acquire_calls["ai"] += 1
            if acquire_calls["ai"] >= 2:
                raise RuntimeError("pool exhausted")
            yield make_ai_session()

        sql_agent = _RecordingSqlAgent([{"service_id": "S1"}])
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("답변"),
        )

        with (
            patch("agents.nodes.data_session_ctx", good_data_ctx),
            patch("agents.nodes.ai_session_ctx", failing_ai_ctx),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            result = await graph.run(_state(message_id=777))

        # 세션 획득 실패가 그래프로 전파되지 않고 answer 정상.
        assert result["output"]["answer"] == "답변"
        # trace node_path 는 그대로 반환된다(best-effort 종단).
        assert "trace" in result["node_path"]
        # trace 세션 획득이 실제로 시도되고 실패 경로를 탔다.
        assert acquire_calls["ai"] >= 2


class TestRetrySessionReacquire:
    async def test_retry_path_reacquires_sessions(self):
        """SQL 0건 → retry → forced VECTOR 경로에서 노드가 세션을 재획득한다.

        sql_node(data) → hydration(data) → [retry] → vector_node(ai) → hydration(data)
        로, data_session ctx 가 여러 번 acquire 된다. 모두 정상 동작해야 한다.
        """
        sql_agent = _RecordingSqlAgent([])  # 0건 → 재시도 유발

        vector_agent = VectorAgent.__new__(VectorAgent)
        refine_chain = MagicMock()
        refine_chain.ainvoke = AsyncMock(
            return_value=_RefinedQuery(
                refined_query="정제",
                max_class_name=None,
                area_name=None,
                service_status=None,
            )
        )
        vector_agent._refine_chain = refine_chain
        embeddings = MagicMock()
        embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
        vector_agent._embeddings = embeddings

        vrows = [{"service_id": "V1", "service_name": "체험관", "similarity": 0.9}]

        # data_session ctx 는 acquire 마다 고유 세션을 돌려준다(재획득 관측).
        data_sessions = tuple(MagicMock(name=f"data{i}") for i in range(4))

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "V1"}]),
            ),
            patch_node_sessions(
                data_sessions=data_sessions,
                ai_session=make_ai_session(),
            ) as (data_ctx, _ai_ctx),
        ):
            graph = AgentGraph(
                router=make_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=make_answer_agent("체험관 안내"),
            )
            result = await graph.run(_state())

        assert result["retry_count"] == 1
        assert result["output"]["answer"] == "체험관 안내"
        # data_session ctx 가 여러 번 acquire 됐다(sql + hydration + 재시도 hydration).
        assert len(data_ctx.used) >= 2
