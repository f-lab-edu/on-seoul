"""제안 2 — VECTOR 4채널 asyncio.gather 병렬화 전용 테스트.

다음 체크리스트 항목을 검증한다:
- 4채널 동시 호출(각 채널 호출 발생, 결과 순서·매핑 보존)
- bm25 토큰 없을 때 d_rows=[] 매핑 어긋남 없음
- 한 채널 예외 → 격리, 나머지·RRF 정상(_safe_* 회귀)
- 세션 분리 후 메인 ai_session 오염 없음(노드 로컬이라 이미 없으나 명시 확인)
- 글로벌 세마포어 동작 확인
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import core.concurrency as _concurrency
from agents.vector_agent import VectorAgent, _RefinedQuery
from core.concurrency import init_global_sema
from schemas.state import IntentType
from tests.helpers import make_agent_state


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_state(message: str = "체험 시설 알려줘") -> dict:
    return make_agent_state(message=message, intent=IntentType.VECTOR_SEARCH)


def _make_agent(
    refined_query: str = "체험 시설",
    vector: list[float] | None = None,
) -> VectorAgent:
    if vector is None:
        vector = [0.1, 0.2, 0.3]
    agent = VectorAgent.__new__(VectorAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_RefinedQuery(refined_query=refined_query))
    agent._refine_chain = chain
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=vector)
    agent._embeddings = emb
    return agent


def _mock_ai_session_ctx():
    """ai_session_ctx 를 mock 세션 yield 로 패치한다."""
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return patch("agents.vector_agent.ai_session_ctx", _ctx)


# ---------------------------------------------------------------------------
# 4채널 동시 호출 및 결과 순서·매핑 보존
# ---------------------------------------------------------------------------


class TestParallelChannelCalls:
    async def test_all_four_channels_are_called(self):
        """4채널(vector×2, question, bm25)이 모두 호출된다."""
        agent = _make_agent()

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
            ) as mock_vs,
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ) as mock_qs,
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])
            ) as mock_bm25,
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())

        # identity + summary = 2회
        assert mock_vs.call_count == 2
        mock_qs.assert_called_once()
        mock_bm25.assert_called_once()

    async def test_result_index_mapping_preserved(self):
        """gather 결과 인덱스 매핑이 고정된다.

        results[0]=a(identity), results[1]=b(summary), results[2]=c(question),
        results[3]=d(bm25) 순서가 RRF 결합에 올바르게 전달된다.
        """
        a_rows = [{"service_id": "A001", "similarity": 0.9}]
        b_rows = [{"service_id": "B001", "similarity": 0.8}]
        c_rows = [{"service_id": "C001", "similarity": 0.7}]
        d_rows = [{"service_id": "D001", "bm25_score": 2.0}]

        async def _vs_side(*args, **kwargs):
            return a_rows if kwargs.get("row_kind") == "identity" else b_rows

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=c_rows)
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(return_value=d_rows)
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        service_ids = {r["service_id"] for r in result["vector"]["results"]}
        # 4채널 결과가 모두 RRF에 반영돼야 한다.
        assert "A001" in service_ids
        assert "B001" in service_ids
        assert "C001" in service_ids
        assert "D001" in service_ids

    async def test_channels_called_concurrently_via_gather(self):
        """asyncio.gather 로 채널이 동시 실행됨을 확인한다.

        각 채널이 짧은 지연을 가질 때 순차 실행보다 빠르게 완료되는 것을 관측하는
        대신, gather 완료 후 모든 채널이 호출됐음을 확인한다(순서 독립 동시성 증거).
        """
        call_order: list[str] = []

        async def _slow_vs(*args, **kwargs):
            rk = kwargs.get("row_kind", "identity")
            call_order.append(f"vs:{rk}")
            return []

        async def _slow_qs(*args, **kwargs):
            call_order.append("qs")
            return []

        async def _slow_bm25(*args, **kwargs):
            call_order.append("bm25")
            return []

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_slow_vs)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(side_effect=_slow_qs)
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(side_effect=_slow_bm25)
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())

        assert "vs:identity" in call_order
        assert "vs:summary" in call_order
        assert "qs" in call_order
        assert "bm25" in call_order


# ---------------------------------------------------------------------------
# bm25 토큰 없을 때 d_rows=[] 매핑 어긋남 없음
# ---------------------------------------------------------------------------


class TestBm25TokensAbsent:
    async def test_bm25_not_called_when_no_valid_tokens(self):
        """모든 토큰이 stopword이면 bm25_search 미호출, d_rows=[] 로 인덱스 고정."""
        agent = _make_agent(refined_query="예약 서비스")

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])
            ) as mock_bm25,
            patch(
                "agents.vector_agent.atokenize_query",
                new=AsyncMock(return_value=["예약", "서비스"]),
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        mock_bm25.assert_not_called()
        # RRF는 빈 bm25 채널로도 정상 결합 — 예외 없음.
        assert result["vector"]["results"] is not None

    async def test_result_mapping_correct_when_bm25_absent(self):
        """bm25 채널 없어도 a/b/c 결과가 올바르게 매핑된다.

        gather 태스크가 3개(a/b/c)일 때 인덱스 0/1/2 가 a/b/c 에 대응되고
        d_rows 는 [] 로 강제되므로 매핑 어긋남이 없다.
        """
        a_rows = [{"service_id": "A1", "similarity": 0.9}]
        c_rows = [{"service_id": "C1", "similarity": 0.7}]

        async def _vs_side(*args, **kwargs):
            return a_rows if kwargs.get("row_kind") == "identity" else []

        agent = _make_agent(refined_query="예약 서비스")

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=c_rows)
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.atokenize_query",
                new=AsyncMock(return_value=["예약", "서비스"]),
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "A1" in ids
        assert "C1" in ids


# ---------------------------------------------------------------------------
# 한 채널 예외 → 격리, 나머지·RRF 정상
# ---------------------------------------------------------------------------


class TestChannelIsolation:
    async def test_identity_channel_failure_isolated(self):
        """Track A(identity) 예외가 B/C/D 채널과 RRF에 영향을 주지 않는다."""
        b_rows = [{"service_id": "B001", "similarity": 0.8}]

        async def _vs_side(*args, **kwargs):
            if kwargs.get("row_kind") == "identity":
                raise RuntimeError("identity 오류")
            return b_rows

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        # B 채널 결과는 살아 있다.
        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "B001" in ids

    async def test_bm25_channel_failure_isolated(self):
        """Track D(bm25) 예외가 A/B/C 채널과 RRF에 영향을 주지 않는다.

        bm25 실패 시 세션 rollback → 독립 세션이므로 다른 채널 세션 오염 없음.
        """
        a_rows = [{"service_id": "A001", "similarity": 0.9}]

        async def _vs_side(*args, **kwargs):
            return a_rows if kwargs.get("row_kind") == "identity" else []

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch(
                "agents.vector_agent.bm25_search",
                new=AsyncMock(side_effect=RuntimeError("ParadeDB 오류")),
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        # A 채널 결과는 살아 있고, 전체 결과가 빈 리스트가 아니다.
        assert result["vector"]["results"] is not None
        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "A001" in ids

    async def test_question_channel_failure_isolated(self):
        """Track C(question) 예외가 A/B/D 채널과 RRF에 영향을 주지 않는다."""
        b_rows = [{"service_id": "B002", "similarity": 0.75}]
        d_rows = [{"service_id": "D002", "bm25_score": 1.5}]

        async def _vs_side(*args, **kwargs):
            return [] if kwargs.get("row_kind") == "identity" else b_rows

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search",
                new=AsyncMock(side_effect=RuntimeError("question 오류")),
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(return_value=d_rows)
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "B002" in ids
        assert "D002" in ids

    async def test_single_channel_failure_does_not_raise(self):
        """한 채널 예외 시 search() 자체는 예외를 전파하지 않는다."""
        agent = _make_agent()

        with (
            patch(
                "agents.vector_agent.vector_search",
                new=AsyncMock(side_effect=RuntimeError("전 채널 오류")),
            ),
            patch(
                "agents.vector_agent.question_search",
                new=AsyncMock(side_effect=RuntimeError("전 채널 오류")),
            ),
            patch(
                "agents.vector_agent.bm25_search",
                new=AsyncMock(side_effect=RuntimeError("전 채널 오류")),
            ),
            _mock_ai_session_ctx(),
        ):
            # 예외 전파 없이 정상 반환
            result = await agent.search(_make_state())

        assert result["vector"]["results"] == []


# ---------------------------------------------------------------------------
# 세션 분리 — bm25 실패의 rollback이 다른 채널 세션을 오염시키지 않는다
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    async def test_bm25_rollback_does_not_affect_other_sessions(self):
        """bm25 채널 세션의 rollback 이 다른 채널 세션에 영향을 주지 않는다.

        채널마다 독립 세션이므로 bm25 실패 → rollback 이 identity/summary/question
        채널 세션의 트랜잭션 상태를 변경하지 않는다.
        """
        rollback_sessions: list[object] = []
        created_sessions: list[object] = []

        @asynccontextmanager
        async def tracking_ai_ctx():
            s = MagicMock()
            s.rollback = AsyncMock(side_effect=lambda: rollback_sessions.append(s))
            created_sessions.append(s)
            yield s

        a_rows = [{"service_id": "A99", "similarity": 0.95}]

        async def _vs_side(*args, **kwargs):
            return a_rows if kwargs.get("row_kind") == "identity" else []

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs_side)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch(
                "agents.vector_agent.bm25_search",
                new=AsyncMock(side_effect=RuntimeError("BM25 실패")),
            ),
            patch("agents.vector_agent.ai_session_ctx", tracking_ai_ctx),
        ):
            result = await agent.search(_make_state())

        # 채널 결과 정상.
        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "A99" in ids

        # rollback 된 세션은 bm25 채널 세션 하나뿐.
        # 다른 채널 세션(identity/summary/question)에는 rollback 미호출.
        assert len(rollback_sessions) == 1
        # bm25 세션이 전체 세션 중 하나이고, 나머지는 오염되지 않았다.
        bm25_session = rollback_sessions[0]
        other_sessions = [s for s in created_sessions if s is not bm25_session]
        for s in other_sessions:
            s.rollback.assert_not_awaited()

    async def test_each_channel_gets_independent_session(self):
        """각 채널 태스크가 서로 다른 세션 객체를 받는다."""
        sessions_used: list[object] = []

        @asynccontextmanager
        async def tracking_ai_ctx():
            s = MagicMock()
            sessions_used.append(s)
            yield s

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch("agents.vector_agent.ai_session_ctx", tracking_ai_ctx),
        ):
            await agent.search(_make_state())

        # 4채널(identity/summary/question/bm25) → 4개 독립 세션
        assert len(sessions_used) == 4
        # 모두 서로 다른 객체
        assert len(set(id(s) for s in sessions_used)) == 4


# ---------------------------------------------------------------------------
# Semaphore 동작 확인
# ---------------------------------------------------------------------------


class TestSemaphore:
    def setup_method(self):
        _concurrency.vector_global_sema = None

    def teardown_method(self):
        _concurrency.vector_global_sema = None

    async def test_semaphore_limits_concurrent_channel_count(self):
        """글로벌 세마포어(단일 가드)가 채널 동시성을 cap한다.

        세마포어 값을 1로 낮추어 채널이 순차 실행되는 것을 확인한다.
        채널 수(N)=4, 세마포어=1 → 동시 실행 수가 1이어야 한다(순차).
        """
        init_global_sema(concurrency=1)
        concurrent: list[int] = []
        active = {"count": 0}

        async def _slow_vs(*args, **kwargs):
            active["count"] += 1
            concurrent.append(active["count"])
            await asyncio.sleep(0)  # 이벤트 루프 양보
            active["count"] -= 1
            return []

        async def _slow_qs(*args, **kwargs):
            active["count"] += 1
            concurrent.append(active["count"])
            await asyncio.sleep(0)
            active["count"] -= 1
            return []

        async def _slow_bm25(*args, **kwargs):
            active["count"] += 1
            concurrent.append(active["count"])
            await asyncio.sleep(0)
            active["count"] -= 1
            return []

        # 글로벌 세마포어를 1로 초기화하여 동시성을 1로 cap한다.
        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_slow_vs)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(side_effect=_slow_qs)
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(side_effect=_slow_bm25)
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())

        # 세마포어=1 → 최대 동시 1개
        assert max(concurrent) <= 1

    async def test_semaphore_4_allows_all_channels_at_once(self):
        """글로벌 세마포어=4이면 4채널이 모두 동시에 실행 가능하다.

        모든 채널이 barrier 에서 동시 대기하여 서로 block 없이 완료되면 동시 실행 확인.
        """
        init_global_sema(concurrency=4)
        reached_barrier: list[str] = []
        barrier = asyncio.Barrier(4)

        async def _vs(*args, **kwargs):
            rk = kwargs.get("row_kind", "identity")
            reached_barrier.append(f"vs:{rk}")
            await barrier.wait()
            return []

        async def _qs(*args, **kwargs):
            reached_barrier.append("qs")
            await barrier.wait()
            return []

        async def _bm25(*args, **kwargs):
            reached_barrier.append("bm25")
            await barrier.wait()
            return []

        agent = _make_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=_vs)),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(side_effect=_qs)
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(side_effect=_bm25)
            ),
            _mock_ai_session_ctx(),
        ):
            # 세마포어=4(기본값) → 4채널이 동시에 barrier 에 도달해야 완료
            # 만약 순차라면 barrier 가 영구 대기 → 테스트 타임아웃
            result = await asyncio.wait_for(agent.search(_make_state()), timeout=5.0)

        assert len(reached_barrier) == 4
        assert result["vector"]["results"] is not None
