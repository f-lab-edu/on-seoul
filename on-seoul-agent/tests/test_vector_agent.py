"""VectorAgent 단위 테스트.

질의 정제 체인, 임베딩, 하이브리드 검색(vector_search + bm25_search → RRF) 동작을
Mock으로 검증한다.

vector_search와 bm25_search를 모두 patch하여 외부 의존성 없이 동작한다.

제안 2 이후: VectorAgent.search()는 ai_session 인자를 받지 않는다.
내부에서 ai_session_ctx()로 채널별 독립 세션을 열기 때문에
테스트는 agents.vector_agent.ai_session_ctx 를 함께 patch 해야 한다.
"""

import asyncio
from contextlib import asynccontextmanager, ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from tests.helpers import make_agent_state
from agents.vector_agent import VectorAgent, _RefinedQuery, _rrf_merge
from schemas.state import AgentState, IntentType


def _make_state(message: str = "아이랑 체험할 수 있는 시설") -> AgentState:
    return make_agent_state(message=message, intent=IntentType.VECTOR_SEARCH)


def _make_agent(
    refined_query: str,
    vector: list[float],
) -> VectorAgent:
    """VectorAgent를 생성한다. DB 세션과 vector_search/bm25_search는 개별 테스트에서 patch."""
    agent = VectorAgent.__new__(VectorAgent)

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(
        return_value=_RefinedQuery(refined_query=refined_query)
    )
    agent._refine_chain = mock_chain

    mock_embeddings = MagicMock()
    mock_embeddings.aembed_query = AsyncMock(return_value=vector)
    agent._embeddings = mock_embeddings

    # __new__ 가 __init__ 을 건너뛰므로 _channel_sema 를 직접 설정한다.
    agent._channel_sema = asyncio.Semaphore(4)

    return agent


def _mock_ai_session_ctx():
    """agents.vector_agent.ai_session_ctx 를 mock 세션을 yield 하도록 패치한다.

    제안 2 이후 VectorAgent.search() 내부에서 채널별 ai_session_ctx() 를 사용하므로
    단위 테스트는 이 패치를 통해 세션 연결 없이 동작한다.
    """
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return patch("agents.vector_agent.ai_session_ctx", _ctx)


def _patch_search(vector_rows: list[dict], bm25_rows: list[dict]):
    """vector_search, question_search, bm25_search, ai_session_ctx 를 동시에 patch.

    Phase 2: hydrate_services 는 HydrationNode 책임이므로 여기서 patch 하지 않는다.
    vector_results 는 메타데이터 only ({service_id, rrf_score}) 로 반환된다.

    제안 2 이후: ai_session_ctx 도 함께 patch 한다.
    """

    class _Ctx:
        def __enter__(self):
            self._stack = ExitStack()
            self.mock_vs = self._stack.enter_context(
                patch(
                    "agents.vector_agent.vector_search",
                    new=AsyncMock(return_value=vector_rows),
                )
            )
            self.mock_qs = self._stack.enter_context(
                patch(
                    "agents.vector_agent.question_search",
                    new=AsyncMock(return_value=[]),
                )
            )
            self.mock_bm25 = self._stack.enter_context(
                patch(
                    "agents.vector_agent.bm25_search",
                    new=AsyncMock(return_value=bm25_rows),
                )
            )
            self._stack.enter_context(_mock_ai_session_ctx())
            return self

        def __exit__(self, *args):
            self._stack.__exit__(*args)

    return _Ctx()


class TestVectorAgentRouterPostFilter:
    """Router가 state["refined_query"]와 post-filter를 채운 경우, _refine_chain을 skip하고
    state 값을 그대로 vector_search에 전달한다.
    """

    async def test_router_postfilter_forwarded_and_refine_chain_skipped(self):
        """state["refined_query"] 존재 시 state["area_name"] 등 post-filter가 vector_search로 전달된다."""
        agent = VectorAgent.__new__(VectorAgent)
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock()  # 호출되면 안 된다
        agent._refine_chain = mock_chain
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1])
        agent._embeddings = mock_embeddings
        agent._channel_sema = asyncio.Semaphore(4)

        state = _make_state()
        state["plan"]["refined_query"] = "강남구 체육시설"
        state["filters"]["max_class_name"] = "체육시설"
        state["filters"]["area_name"] = "강남구"
        state["filters"]["service_status"] = "접수중"

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
            ) as mock_vs,
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(state)
            # vector_search는 identity(Track A)와 summary(Track B) 두 번 호출된다.
            # identity 호출(row_kind='identity')에서 post-filter가 전달되어야 한다.
            identity_call = next(
                (
                    c
                    for c in mock_vs.call_args_list
                    if c[1].get("row_kind") == "identity"
                ),
                None,
            )
            assert identity_call is not None, "identity row_kind 호출이 없음"
            kwargs = identity_call[1]
            assert kwargs.get("max_class_name") == "체육시설"
            assert kwargs.get("area_name") == "강남구"
            assert kwargs.get("service_status") == "접수중"

        # router가 산출하면 fallback _refine_chain은 호출되지 않는다.
        mock_chain.ainvoke.assert_not_called()

    async def test_refine_chain_used_when_router_did_not_refine(self):
        """state["refined_query"]=None이면 _refine_chain이 fallback으로 호출된다."""
        agent = VectorAgent.__new__(VectorAgent)
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(
            return_value=_RefinedQuery(refined_query="자체정제")
        )
        agent._refine_chain = mock_chain
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1])
        agent._embeddings = mock_embeddings
        agent._channel_sema = asyncio.Semaphore(4)

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())

        mock_chain.ainvoke.assert_called_once()


class TestVectorAgentPostFilter:
    """vector_search 호출 시 post-filter 파라미터 전달 검증."""

    async def test_postfilter_params_forwarded_when_refined_query_has_filters(self):
        """_RefinedQuery에서 추출된 필터 파라미터가 vector_search 키워드 인자로 전달된다."""
        from agents.vector_agent import _RefinedQuery

        agent = VectorAgent.__new__(VectorAgent)
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(
            return_value=_RefinedQuery(
                refined_query="체육 시설",
                max_class_name="체육",
                area_name="강남구",
                service_status="접수중",
            )
        )
        agent._refine_chain = mock_chain
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1, 0.2])
        agent._embeddings = mock_embeddings
        agent._channel_sema = asyncio.Semaphore(4)

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
            ) as mock_vs,
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())
            # identity 호출의 kwargs에서 post-filter 확인
            identity_call = next(
                (
                    c
                    for c in mock_vs.call_args_list
                    if c[1].get("row_kind") == "identity"
                ),
                None,
            )
            assert identity_call is not None
            kwargs = identity_call[1]
            assert kwargs.get("max_class_name") == "체육"
            assert kwargs.get("area_name") == "강남구"
            assert kwargs.get("service_status") == "접수중"

    async def test_none_filters_not_forwarded_when_absent(self):
        """_RefinedQuery 필터가 None이면 vector_search 키워드 인자도 None이다."""
        from agents.vector_agent import _RefinedQuery

        agent = VectorAgent.__new__(VectorAgent)
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(
            return_value=_RefinedQuery(
                refined_query="체험 시설",
                max_class_name=None,
                area_name=None,
                service_status=None,
            )
        )
        agent._refine_chain = mock_chain
        mock_embeddings = MagicMock()
        mock_embeddings.aembed_query = AsyncMock(return_value=[0.1])
        agent._embeddings = mock_embeddings
        agent._channel_sema = asyncio.Semaphore(4)

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
            ) as mock_vs,
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())
            # identity 호출의 kwargs에서 post-filter None 확인
            identity_call = next(
                (
                    c
                    for c in mock_vs.call_args_list
                    if c[1].get("row_kind") == "identity"
                ),
                None,
            )
            assert identity_call is not None
            kwargs = identity_call[1]
            assert kwargs.get("max_class_name") is None
            assert kwargs.get("area_name") is None
            assert kwargs.get("service_status") is None


class TestVectorAgent:
    async def test_search_populates_vector_results(self):
        """search는 RRF 결합 결과를 vector_results에 채운다."""
        vector_rows = [
            {"service_id": "S001", "service_name": "어린이 체험관", "similarity": 0.85}
        ]
        agent = _make_agent("어린이 체험 시설", [0.1, 0.2])

        with _patch_search(vector_rows, []):
            result = await agent.search(_make_state())

        assert result["vector"]["results"] is not None
        assert len(result["vector"]["results"]) >= 1
        assert result["vector"]["results"][0]["service_id"] == "S001"

    async def test_search_populates_refined_query(self):
        """search는 정제된 질의를 refined_query에 채운다."""
        agent = _make_agent("어린이 체험 시설", [0.1])

        with _patch_search([], []):
            result = await agent.search(_make_state())

        assert result["plan"]["refined_query"] == "어린이 체험 시설"

    async def test_similarity_search_passes_query_vector(self):
        """vector_search에 query_vector가 전달된다."""
        vector = [0.1, 0.2, 0.3]
        agent = _make_agent("정제된 쿼리", vector)

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
            ) as mock_vs,
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            _mock_ai_session_ctx(),
        ):
            await agent.search(_make_state())
            # 첫 번째 호출(identity)의 positional arg에서 query_vector 확인
            _, call_vector = mock_vs.call_args_list[0][0]
            assert call_vector == vector

    async def test_search_returns_empty_vector_results_when_no_rows(self):
        """vector_search와 bm25_search 모두 빈 리스트를 반환하면 vector_results는 빈 리스트다."""
        agent = _make_agent("정제된 쿼리", [0.1])

        with _patch_search([], []):
            result = await agent.search(_make_state())

        assert result["vector"]["results"] == []
        assert result["vector"]["results"] is not None

    async def test_bm25_skipped_when_all_tokens_are_stopwords(self):
        """모든 토큰이 stopword이면 bm25_search를 호출하지 않는다."""
        agent = _make_agent("예약 서비스", [0.1])

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
            await agent.search(_make_state())
            mock_bm25.assert_not_called()

    async def test_bm25_channel_query_text_is_none_when_tokens_empty(self):
        """bm25_tokens 가 빈 리스트일 때 BM25 채널의 query_text 가 None 이다."""
        from schemas.search import SearchChannel

        agent = _make_agent("예약 서비스", [0.1])

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.atokenize_query",
                new=AsyncMock(return_value=["예약", "서비스"]),
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        bm25_channel = result["search_channels"][SearchChannel.BM25]
        assert bm25_channel["query"]["query_text"] is None


class TestHybridSearchRrf:
    async def test_hybrid_result_contains_rrf_score(self):
        """하이브리드 검색 결과 dict에 rrf_score 키가 포함된다."""
        vector_rows = [
            {"service_id": "S001", "service_name": "체험관", "similarity": 0.85}
        ]
        bm25_rows = [{"service_id": "S001", "bm25_score": 2.5}]
        agent = _make_agent("체험 시설", [0.1])

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state())

        assert "rrf_score" in result["vector"]["results"][0]

    async def test_rrf_merges_both_results(self):
        """vector_search와 bm25_search에 각각 다른 service_id가 있으면 모두 결합된다."""
        vector_rows = [
            {"service_id": "S001", "service_name": "체험관", "similarity": 0.85}
        ]
        bm25_rows = [{"service_id": "S002", "bm25_score": 2.5}]
        agent = _make_agent("체험 시설", [0.1])

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state())

        service_ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "S001" in service_ids
        assert "S002" in service_ids

    async def test_bm25_only_result_included_in_vector_results(self):
        """BM25 전용 결과(벡터 검색에 없는 service_id)가 vector_results 에 포함된다.

        Phase 2: vector_results 는 메타데이터 only — service_id + rrf_score 만 보장.
        원본 필드(service_name 등)는 HydrationNode 가 채운다.
        """
        vector_rows = [
            {"service_id": "S001", "service_name": "체험관", "similarity": 0.85}
        ]
        bm25_rows = [
            {"service_id": "S002", "service_name": "한강수영장", "bm25_score": 3.0}
        ]
        agent = _make_agent("한강 수영", [0.1])

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state())

        service_ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "S002" in service_ids
        bm25_only = next(
            r for r in result["vector"]["results"] if r["service_id"] == "S002"
        )
        assert "rrf_score" in bm25_only

    async def test_rrf_boost_for_overlap(self):
        """두 검색 결과에 모두 등장한 service_id가 더 높은 rrf_score를 갖는다."""
        vector_rows = [
            {"service_id": "S001", "service_name": "체험관", "similarity": 0.85},
            {"service_id": "S002", "service_name": "수영장", "similarity": 0.75},
        ]
        bm25_rows = [
            {"service_id": "S001", "bm25_score": 2.5},
            {"service_id": "S003", "bm25_score": 1.5},
        ]
        agent = _make_agent("체험 시설", [0.1])

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state())

        scores = {r["service_id"]: r["rrf_score"] for r in result["vector"]["results"]}
        # S001은 두 결과에 모두 등장 → 가장 높은 점수
        assert scores["S001"] > scores["S002"]
        assert scores["S001"] > scores["S003"]


class TestVectorAgentSearchFailure:
    """vector_search 또는 bm25_search 실패 시 예외 격리 및 RRF 결합 동작 검증."""

    async def test_vector_search_failure_falls_back_to_bm25_only(self):
        """vector_search가 예외를 발생시키면 bm25 결과만으로 RRF가 수행된다."""
        bm25_rows = [{"service_id": "S010", "bm25_score": 3.0}]
        agent = _make_agent("정제된 쿼리", [0.1])

        with (
            patch(
                "agents.vector_agent.vector_search",
                new=AsyncMock(side_effect=RuntimeError("DB 연결 오류")),
            ),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch(
                "agents.vector_agent.bm25_search", new=AsyncMock(return_value=bm25_rows)
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        assert result["vector"]["results"] is not None
        service_ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "S010" in service_ids

    async def test_bm25_search_failure_falls_back_to_vector_only(self):
        """bm25_search가 예외를 발생시키면 vector 결과만으로 RRF가 수행된다."""
        vector_rows = [
            {"service_id": "S020", "service_name": "수영장", "similarity": 0.9}
        ]
        agent = _make_agent("정제된 쿼리", [0.1])

        with (
            patch(
                "agents.vector_agent.vector_search",
                new=AsyncMock(return_value=vector_rows),
            ),
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

        assert result["vector"]["results"] is not None
        service_ids = {r["service_id"] for r in result["vector"]["results"]}
        assert "S020" in service_ids

    async def test_both_search_failure_returns_empty_vector_results(self):
        """vector_search와 bm25_search 모두 실패하면 vector_results는 빈 리스트다."""
        agent = _make_agent("정제된 쿼리", [0.1])

        with (
            patch(
                "agents.vector_agent.vector_search",
                new=AsyncMock(side_effect=RuntimeError("DB 오류")),
            ),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch(
                "agents.vector_agent.bm25_search",
                new=AsyncMock(side_effect=RuntimeError("BM25 오류")),
            ),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        assert result["vector"]["results"] == []


class TestServiceStatusValidation:
    """_RefinedQuery service_status 도메인 검증."""

    def test_valid_service_status_accepted(self):
        """허용된 service_status 값은 그대로 유지된다."""
        rq = _RefinedQuery(refined_query="체육 시설", service_status="접수중")
        assert rq.service_status == "접수중"

    def test_invalid_service_status_becomes_none(self):
        """허용되지 않은 service_status 값은 None으로 대체된다."""
        rq = _RefinedQuery(refined_query="체육 시설", service_status="신청가능")
        assert rq.service_status is None


class TestRrfMergeTopKConstant:
    """_rrf_merge 호출 시 _TOP_K 상수가 명시적으로 전달된다."""

    async def test_rrf_merge_uses_top_k_constant(self):
        """search 결과가 _TOP_K 이하로 제한된다."""
        from agents.vector_agent import _TOP_K

        # _TOP_K + 5개의 vector 결과를 생성하여 제한이 적용되는지 확인
        vector_rows = [
            {"service_id": f"S{i:03d}", "service_name": f"X{i}", "similarity": 0.9}
            for i in range(_TOP_K + 5)
        ]
        agent = _make_agent("정제됨", [0.1])

        with _patch_search(vector_rows, []):
            result = await agent.search(_make_state())

        assert len(result["vector"]["results"]) <= _TOP_K


class TestRrfMerge:
    def test_empty_both_returns_empty(self):
        """두 결과가 모두 빈 리스트이면 빈 리스트를 반환한다."""
        assert _rrf_merge([], []) == []

    def test_vector_only(self):
        """vector_rows만 있을 때 RRF 점수가 정상 계산된다."""
        vector_rows = [
            {"service_id": "S001", "service_name": "A", "similarity": 0.9},
            {"service_id": "S002", "service_name": "B", "similarity": 0.8},
        ]
        result = _rrf_merge(vector_rows, [])
        assert len(result) == 2
        assert result[0]["service_id"] == "S001"
        assert result[0]["rrf_score"] > result[1]["rrf_score"]

    def test_bm25_only(self):
        """bm25_rows만 있을 때 RRF 점수가 정상 계산된다."""
        bm25_rows = [
            {"service_id": "S001", "bm25_score": 3.0},
            {"service_id": "S002", "bm25_score": 1.5},
        ]
        result = _rrf_merge([], bm25_rows)
        assert len(result) == 2
        assert result[0]["service_id"] == "S001"

    def test_overlap_gets_higher_score(self):
        """두 결과에 모두 등장한 service_id가 단독 등장보다 높은 점수를 갖는다."""
        vector_rows = [{"service_id": "S001", "service_name": "A", "similarity": 0.9}]
        bm25_rows = [
            {"service_id": "S001", "bm25_score": 3.0},
            {"service_id": "S002", "bm25_score": 2.5},
        ]
        result = _rrf_merge(vector_rows, bm25_rows)
        scores = {r["service_id"]: r["rrf_score"] for r in result}
        assert scores["S001"] > scores["S002"]

    def test_top_k_limits_result(self):
        """top_k 파라미터가 반환 결과 수를 제한한다."""
        vector_rows = [
            {"service_id": f"S{i:03d}", "service_name": f"X{i}", "similarity": 0.9}
            for i in range(20)
        ]
        result = _rrf_merge(vector_rows, [], top_k=5)
        assert len(result) == 5

    def test_rrf_score_formula(self):
        """RRF 점수가 1/(k+rank) 공식을 따른다."""
        k = 60
        vector_rows = [{"service_id": "S001", "service_name": "A", "similarity": 0.9}]
        result = _rrf_merge(vector_rows, [], k=k)
        expected_score = 1.0 / (k + 1)
        assert abs(result[0]["rrf_score"] - expected_score) < 1e-9

    def test_result_preserves_vector_metadata(self):
        """RRF 결과에 vector_search의 service_name, metadata 등이 보존된다."""
        vector_rows = [
            {
                "service_id": "S001",
                "service_name": "체험관",
                "metadata": {"area_name": "강남구"},
                "similarity": 0.85,
            }
        ]
        result = _rrf_merge(vector_rows, [])
        assert result[0]["service_name"] == "체험관"
        assert result[0]["metadata"] == {"area_name": "강남구"}


class TestVectorAgentMetaOnlyResults:
    """Phase 2: vector_results 는 메타데이터 only — HydrationNode 가 원본을 채운다."""

    async def test_vector_results_contains_service_id_and_rrf_score_only(self):
        """vector_results 각 행은 service_id 와 rrf_score 만 포함한다."""
        vector_rows = [
            {
                "service_id": "S001",
                "service_name": "n",
                "metadata": {},
                "similarity": 0.9,
            }
        ]
        agent = VectorAgent.__new__(VectorAgent)
        refined = MagicMock()
        refined.refined_query = "수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain = MagicMock()
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)
        agent._channel_sema = asyncio.Semaphore(4)

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(make_agent_state(message="수영장"))

        assert len(result["vector"]["results"]) == 1
        row = result["vector"]["results"][0]
        assert row["service_id"] == "S001"
        assert "rrf_score" in row
        # 원본 필드는 포함되지 않는다 — HydrationNode 책임
        assert "service_name" not in row
        assert "service_status" not in row

    async def test_multiple_vector_rows_all_appear_in_meta_results(self):
        """여러 검색 결과가 모두 vector_results 에 포함된다 (service_id 기준)."""
        vector_rows = [
            {"service_id": "S001", "similarity": 0.9},
            {"service_id": "S002", "similarity": 0.8},
        ]
        agent = _make_agent("수영장", [0.1])

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        ids = {r["service_id"] for r in result["vector"]["results"]}
        assert ids == {"S001", "S002"}
