"""제안 0 / 0-6 — 동시 요청 격리 회귀 테스트.

GraphNodes 는 컨테이너당 싱글톤(무상태)이다. DB 세션은 노드 로컬(0-6, 노드 내부
`*_session_ctx()` 로 acquire-use-release)로, node_path 는 AgentState reducer 로
per-request 격리된다.

이 파일은 다음을 봉인한다:
1. 두 요청을 asyncio 로 동시 실행해 인터리빙을 강제할 때, 각 요청이 자기 세션만
   사용하고 상대 세션을 잡지 않는다(요청 간 DB 세션 교차 = 데이터 누수 방지).
2. node_path 가 요청별로 누적되며 서로 섞이지 않는다.
3. node_path reducer 가 노드 경로 순서를 보존하며 누적한다.
4. self-correction 재시도 경로에서도 node_path 가 정상 누적된다.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.sql_agent import SqlAgent, _SqlParams
from schemas.state import IntentType, node_path_reducer
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_router,
    run_graph,
)


def _state(**kwargs):
    return make_agent_state(**kwargs)


class _RecordingSqlAgent(SqlAgent):
    """search() 가 받은 data_session 을 기록하고, 배리어로 인터리빙을 강제한다."""

    def __init__(self, rows, *, barrier: asyncio.Barrier | None = None):
        self._chain = MagicMock()
        self._chain.ainvoke = AsyncMock(return_value=_SqlParams(keyword="kw"))
        self._rows = rows
        self._barrier = barrier
        self.seen_sessions: list[object] = []

    async def search(self, state, data_session):
        # 어느 세션을 받았는지 기록한다 (요청 간 교차 탐지용).
        self.seen_sessions.append(data_session)
        if self._barrier is not None:
            # 두 요청이 모두 search 안에 들어올 때까지 대기 →
            # 과거 싱글톤 self.data_session 설계라면 여기서 세션이 덮어써진다.
            await self._barrier.wait()
        return {**state, "sql_results": self._rows, "sql_keyword": "kw"}


def _unique_session_ctx(prefix: str):
    """호출마다 고유 MagicMock 세션을 yield 하는 asynccontextmanager.

    노드 로컬 세션(0-6): 노드는 풀에서 매번 별개 세션을 잡는다. 이를 모사해 동시
    요청이 서로 다른 세션 객체를 받는지(교차 없음) 검증한다.
    """
    counter = {"n": 0}

    @asynccontextmanager
    async def _ctx():
        counter["n"] += 1
        yield MagicMock(name=f"{prefix}{counter['n']}")

    return _ctx


class TestConcurrentRequestIsolation:
    async def test_two_concurrent_requests_use_own_sessions(self):
        """공유 그래프에서 두 요청을 동시 실행 시 각 요청이 자기 노드 로컬 세션만 쓴다.

        노드 로컬 세션(0-6) 전환 후 노드는 `data_session_ctx()` 로 풀에서 세션을
        잡는다. 호출마다 고유 세션을 돌려주는 ctx 로 패치하고, barrier 로 두 요청을
        sql search 안에서 동시에 머무르게 한 뒤(과거 싱글톤 버그 재현 타이밍), 각
        요청의 search 가 받은 세션 집합이 서로 겹치지 않음을 단언한다(교차 없음).
        """
        barrier = asyncio.Barrier(2)
        sql_a = _RecordingSqlAgent(
            [{"service_id": "S1", "service_name": "수영장"}], barrier=barrier
        )
        sql_b = _RecordingSqlAgent(
            [{"service_id": "S2", "service_name": "헬스장"}], barrier=barrier
        )

        # 컨테이너당 싱글톤 그래프를 모사하되, 요청별 sql_agent 로 seen_sessions 를 분리 관측.
        graph_a = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_a,
            answer_agent=make_answer_agent("답변"),
        )
        graph_b = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_b,
            answer_agent=make_answer_agent("답변"),
        )

        data_ctx = _unique_session_ctx("data")
        ai_ctx = _unique_session_ctx("ai")

        with (
            patch("agents.nodes.data_session_ctx", data_ctx),
            patch("agents.nodes.ai_session_ctx", ai_ctx),
            patch("agents.hydration_node.hydrate_services", AsyncMock(return_value=[])),
        ):
            res_a, res_b = await asyncio.gather(
                graph_a.run(_state(room_id=1, message_id=1)),
                graph_b.run(_state(room_id=2, message_id=2)),
            )

        # 두 요청의 sql search 세션 집합이 서로 겹치지 않는다(교차/유실 없음).
        ids_a = set(map(id, sql_a.seen_sessions))
        ids_b = set(map(id, sql_b.seen_sessions))
        assert ids_a and ids_b
        assert ids_a.isdisjoint(ids_b)
        assert res_a["answer"] == "답변"
        assert res_b["answer"] == "답변"

    async def test_concurrent_requests_node_paths_do_not_cross(self):
        """동시 요청의 node_path 가 서로 섞이지 않고 각자 완전한 경로를 가진다."""
        barrier = asyncio.Barrier(2)
        # 0건이면 self-correction 재시도가 끼어 경로가 길어지므로 hit 1건으로 단순화.
        sql_a = _RecordingSqlAgent([{"service_id": "S1"}], barrier=barrier)
        sql_b = _RecordingSqlAgent([{"service_id": "S2"}], barrier=barrier)

        graph_a = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_a,
            answer_agent=make_answer_agent("답변"),
        )
        graph_b = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_b,
            answer_agent=make_answer_agent("답변"),
        )

        with (
            patch("agents.nodes.data_session_ctx", _unique_session_ctx("data")),
            patch("agents.nodes.ai_session_ctx", _unique_session_ctx("ai")),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "S1"}]),
            ),
        ):
            res_a, res_b = await asyncio.gather(
                graph_a.run(_state(room_id=1, message_id=1)),
                graph_b.run(_state(room_id=2, message_id=2)),
            )

        # 각 요청의 node_path 는 router → sql → ... → trace 의 자기 경로만 가진다.
        for res in (res_a, res_b):
            path = res["node_path"]
            assert path[0] == "router"
            assert "sql_node" in path
            assert path[-1] == "trace"
            # 동일 노드가 (재시도 없이) 중복 누적되지 않는다 = 상대 요청 경로 미혼입.
            assert path.count("router") == 1
            assert path.count("sql_node") == 1


class TestNodePathReducer:
    def test_accumulates_in_order(self):
        """node_path_reducer 가 부분 리스트를 순서 보존하며 append 누적한다."""
        acc = node_path_reducer(None, ["router"])
        acc = node_path_reducer(acc, ["cache_check_miss"])
        acc = node_path_reducer(acc, ["sql_node"])
        assert acc == ["router", "cache_check_miss", "sql_node"]

    def test_none_and_empty_are_noop(self):
        old = ["router"]
        assert node_path_reducer(old, None) == old
        assert node_path_reducer(old, []) == old
        assert node_path_reducer(None, None) == []

    def test_does_not_reset_on_retry(self):
        """재시도 경로(retry_prep)도 리셋 없이 누적된다 — 전체 경로 관측이 목적."""
        acc = node_path_reducer(None, ["router"])
        acc = node_path_reducer(acc, ["sql_node"])
        acc = node_path_reducer(acc, ["retry_prep"])
        acc = node_path_reducer(acc, ["router"])
        acc = node_path_reducer(acc, ["vector_node"])
        assert acc == ["router", "sql_node", "retry_prep", "router", "vector_node"]


class TestRetryNodePathAccumulation:
    async def test_node_path_accumulates_across_self_correction(self):
        """SQL 0건 → retry_prep → forced VECTOR 전환 경로에서 node_path 가 누적된다.

        node_path 는 리셋하지 않으므로 sql_node 와 vector_node 가 모두 경로에 남고
        retry_prep 가 그 사이에 기록되어 전체 실행 흐름을 관측할 수 있어야 한다.
        """
        from agents.vector_agent import VectorAgent, _RefinedQuery

        sql_agent = _RecordingSqlAgent([])  # SQL 0건 → 재시도 유발

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
        # __new__ 가 __init__ 을 건너뛰므로 _channel_sema 를 직접 설정한다.
        vector_agent._channel_sema = asyncio.Semaphore(4)

        vrows = [{"service_id": "V1", "service_name": "체험관", "similarity": 0.9}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=[{"service_id": "V1"}]),
            ),
        ):
            graph = AgentGraph(
                router=make_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=make_answer_agent("체험관 안내"),
            )
            result = await run_graph(
                graph,
                _state(),
                data_session=MagicMock(),
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        assert "sql_node" in path
        assert "retry_prep" in path
        assert "vector_node" in path
        # 순서 보존: sql → retry_prep → vector (리셋 없이 누적).
        assert (
            path.index("sql_node")
            < path.index("retry_prep")
            < path.index("vector_node")
        )
        assert result["retry_count"] == 1
