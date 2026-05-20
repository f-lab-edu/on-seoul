"""VectorAgent 단위 테스트.

질의 정제 체인, 임베딩, 하이브리드 검색(vector_search + bm25_search → RRF) 동작을
Mock으로 검증한다.

vector_search와 bm25_search를 모두 patch하여 외부 의존성 없이 동작한다.
"""

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
    mock_chain.ainvoke = AsyncMock(return_value=_RefinedQuery(refined_query=refined_query))
    agent._refine_chain = mock_chain

    mock_embeddings = MagicMock()
    mock_embeddings.aembed_query = AsyncMock(return_value=vector)
    agent._embeddings = mock_embeddings

    return agent


def _patch_search(vector_rows: list[dict], bm25_rows: list[dict]):
    """vector_search, bm25_search, hydrate_services를 동시에 patch하는 컨텍스트 매니저.

    hydrate_services는 입력된 service_ids 순서대로 vector_rows/bm25_rows 메타데이터를
    기반으로 hydrated 행을 반환한다 (원본 hydration을 흉내내는 fake).
    """
    from contextlib import ExitStack

    meta_by_id: dict[str, dict] = {}
    for r in vector_rows:
        meta_by_id[r["service_id"]] = dict(r)
    for r in bm25_rows:
        meta_by_id.setdefault(r["service_id"], dict(r))

    async def _fake_hydrate(_session, service_ids: list[str]) -> list[dict]:
        return [dict(meta_by_id[sid]) for sid in service_ids if sid in meta_by_id]

    class _Ctx:
        def __enter__(self):
            self._stack = ExitStack()
            self.mock_vs = self._stack.enter_context(
                patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=vector_rows))
            )
            self.mock_bm25 = self._stack.enter_context(
                patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=bm25_rows))
            )
            self.mock_hydrate = self._stack.enter_context(
                patch("agents.vector_agent.hydrate_services", new=AsyncMock(side_effect=_fake_hydrate))
            )
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

        state = _make_state()
        state["refined_query"] = "강남구 체육시설"
        state["max_class_name"] = "체육시설"
        state["area_name"] = "강남구"
        state["service_status"] = "접수중"

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs, \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])):
            await agent.search(state, MagicMock(), MagicMock())
            kwargs = mock_vs.call_args[1]
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

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])):
            await agent.search(_make_state(), MagicMock(), MagicMock())

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

        mock_session = MagicMock()
        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs, \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])):
            await agent.search(_make_state(), mock_session, MagicMock())
            kwargs = mock_vs.call_args[1]
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

        mock_session = MagicMock()
        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs, \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])):
            await agent.search(_make_state(), mock_session, MagicMock())
            kwargs = mock_vs.call_args[1]
            assert kwargs.get("max_class_name") is None
            assert kwargs.get("area_name") is None
            assert kwargs.get("service_status") is None



class TestVectorAgent:
    async def test_search_populates_vector_results(self):
        """search는 RRF 결합 결과를 vector_results에 채운다."""
        vector_rows = [{"service_id": "S001", "service_name": "어린이 체험관", "similarity": 0.85}]
        agent = _make_agent("어린이 체험 시설", [0.1, 0.2])
        mock_session = MagicMock()

        with _patch_search(vector_rows, []):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["vector_results"] is not None
        assert len(result["vector_results"]) >= 1
        assert result["vector_results"][0]["service_id"] == "S001"

    async def test_search_populates_refined_query(self):
        """search는 정제된 질의를 refined_query에 채운다."""
        agent = _make_agent("어린이 체험 시설", [0.1])
        mock_session = MagicMock()

        with _patch_search([], []):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["refined_query"] == "어린이 체험 시설"

    async def test_similarity_search_passes_query_vector(self):
        """vector_search에 query_vector가 전달된다."""
        vector = [0.1, 0.2, 0.3]
        agent = _make_agent("정제된 쿼리", vector)
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs, \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])):
            await agent.search(_make_state(), mock_session, MagicMock())
            _, call_vector = mock_vs.call_args[0]
            assert call_vector == vector

    async def test_search_returns_empty_vector_results_when_no_rows(self):
        """vector_search와 bm25_search 모두 빈 리스트를 반환하면 vector_results는 빈 리스트다."""
        agent = _make_agent("정제된 쿼리", [0.1])
        mock_session = MagicMock()

        with _patch_search([], []):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["vector_results"] == []
        assert result["vector_results"] is not None

    async def test_bm25_skipped_when_all_tokens_are_stopwords(self):
        """모든 토큰이 stopword이면 bm25_search를 호출하지 않는다."""
        agent = _make_agent("예약 서비스", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])) as mock_bm25, \
             patch("agents.vector_agent.tokenize_query", return_value=["예약", "서비스"]):
            await agent.search(_make_state(), mock_session, MagicMock())
            mock_bm25.assert_not_called()

    async def test_bm25_channel_query_text_is_none_when_tokens_empty(self):
        """bm25_tokens 가 빈 리스트일 때 BM25 채널의 query_text 가 None 이다."""
        from schemas.search import SearchChannel

        agent = _make_agent("예약 서비스", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])), \
             patch("agents.vector_agent.tokenize_query", return_value=["예약", "서비스"]), \
             patch("agents.vector_agent.hydrate_services", new=AsyncMock(return_value=[])):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        bm25_channel = result["search_channels"][SearchChannel.BM25]
        assert bm25_channel["query"]["query_text"] is None


class TestHybridSearchRrf:
    async def test_hybrid_result_contains_rrf_score(self):
        """하이브리드 검색 결과 dict에 rrf_score 키가 포함된다."""
        vector_rows = [{"service_id": "S001", "service_name": "체험관", "similarity": 0.85}]
        bm25_rows = [{"service_id": "S001", "bm25_score": 2.5}]
        agent = _make_agent("체험 시설", [0.1])
        mock_session = MagicMock()

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert "rrf_score" in result["vector_results"][0]

    async def test_rrf_merges_both_results(self):
        """vector_search와 bm25_search에 각각 다른 service_id가 있으면 모두 결합된다."""
        vector_rows = [{"service_id": "S001", "service_name": "체험관", "similarity": 0.85}]
        bm25_rows = [{"service_id": "S002", "bm25_score": 2.5}]
        agent = _make_agent("체험 시설", [0.1])
        mock_session = MagicMock()

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        service_ids = {r["service_id"] for r in result["vector_results"]}
        assert "S001" in service_ids
        assert "S002" in service_ids

    async def test_bm25_only_result_preserves_metadata(self):
        """BM25 전용 결과(벡터 검색에 없는 service_id)의 메타데이터가 유지된다."""
        vector_rows = [{"service_id": "S001", "service_name": "체험관", "similarity": 0.85}]
        bm25_rows = [{"service_id": "S002", "service_name": "한강수영장", "bm25_score": 3.0}]
        agent = _make_agent("한강 수영", [0.1])
        mock_session = MagicMock()

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        bm25_only = next(r for r in result["vector_results"] if r["service_id"] == "S002")
        # BM25 전용 결과도 service_name이 누락되지 않아야 한다
        assert bm25_only.get("service_name") == "한강수영장"
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
        mock_session = MagicMock()

        with _patch_search(vector_rows, bm25_rows):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        scores = {r["service_id"]: r["rrf_score"] for r in result["vector_results"]}
        # S001은 두 결과에 모두 등장 → 가장 높은 점수
        assert scores["S001"] > scores["S002"]
        assert scores["S001"] > scores["S003"]


class TestVectorAgentSearchFailure:
    """vector_search 또는 bm25_search 실패 시 예외 격리 및 RRF 결합 동작 검증."""

    async def test_vector_search_failure_falls_back_to_bm25_only(self):
        """vector_search가 예외를 발생시키면 bm25 결과만으로 RRF가 수행된다."""
        bm25_rows = [{"service_id": "S010", "bm25_score": 3.0}]
        agent = _make_agent("정제된 쿼리", [0.1])
        mock_session = MagicMock()

        hydrated = [{"service_id": "S010", "bm25_score": 3.0}]
        with patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=RuntimeError("DB 연결 오류"))), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=bm25_rows)), \
             patch("agents.vector_agent.hydrate_services", new=AsyncMock(return_value=hydrated)):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["vector_results"] is not None
        service_ids = {r["service_id"] for r in result["vector_results"]}
        assert "S010" in service_ids

    async def test_bm25_search_failure_falls_back_to_vector_only(self):
        """bm25_search가 예외를 발생시키면 vector 결과만으로 RRF가 수행된다."""
        vector_rows = [{"service_id": "S020", "service_name": "수영장", "similarity": 0.9}]
        agent = _make_agent("정제된 쿼리", [0.1])
        mock_session = MagicMock()

        hydrated = [{"service_id": "S020", "service_name": "수영장"}]
        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=vector_rows)), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(side_effect=RuntimeError("ParadeDB 오류"))), \
             patch("agents.vector_agent.hydrate_services", new=AsyncMock(return_value=hydrated)):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["vector_results"] is not None
        service_ids = {r["service_id"] for r in result["vector_results"]}
        assert "S020" in service_ids

    async def test_both_search_failure_returns_empty_vector_results(self):
        """vector_search와 bm25_search 모두 실패하면 vector_results는 빈 리스트다."""
        agent = _make_agent("정제된 쿼리", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(side_effect=RuntimeError("DB 오류"))), \
             patch("agents.vector_agent.bm25_search", new=AsyncMock(side_effect=RuntimeError("BM25 오류"))):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert result["vector_results"] == []


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
        mock_session = MagicMock()

        with _patch_search(vector_rows, []):
            result = await agent.search(_make_state(), mock_session, MagicMock())

        assert len(result["vector_results"]) <= _TOP_K


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
        vector_rows = [{"service_id": f"S{i:03d}", "service_name": f"X{i}", "similarity": 0.9} for i in range(20)]
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
            {"service_id": "S001", "service_name": "체험관", "metadata": {"area_name": "강남구"}, "similarity": 0.85}
        ]
        result = _rrf_merge(vector_rows, [])
        assert result[0]["service_name"] == "체험관"
        assert result[0]["metadata"] == {"area_name": "강남구"}



class TestVectorAgentHydration:
    """RRF 결과의 service_id로 public_service_reservations를 hydrate하는 흐름."""

    async def test_hydrate_called_with_rrf_service_ids(self):
        """RRF 결합 결과의 service_id 리스트가 hydrate_services에 전달된다."""
        # 정제 LLM 모킹
        agent = VectorAgent.__new__(VectorAgent)
        agent._refine_chain = MagicMock()
        refined = MagicMock()
        refined.refined_query = "강남 수영장"
        refined.max_class_name = None
        refined.area_name = None
        refined.service_status = None
        agent._refine_chain.ainvoke = AsyncMock(return_value=refined)

        # 임베딩 모킹
        agent._embeddings = MagicMock()
        agent._embeddings.aembed_query = AsyncMock(return_value=[0.1] * 768)

        ai_session = MagicMock()
        data_session = MagicMock()

        # vector_search, bm25_search, hydrate_services 모킹
        with (
            patch("agents.vector_agent.vector_search",
                  AsyncMock(return_value=[{"service_id": "S001", "service_name": "n", "metadata": {}, "similarity": 0.9}])),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services",
                  AsyncMock(return_value=[{"service_id": "S001", "service_name": "수영장", "service_status": "접수중"}])) as mock_hydrate,
        ):
            state = _make_state("강남 수영장")
            result = await agent.search(state, ai_session, data_session)

            # hydrate_services가 data_session과 RRF 결과 service_id로 호출됨
            mock_hydrate.assert_awaited_once()
            call_args = mock_hydrate.await_args
            assert call_args[0][0] is data_session
            assert call_args[0][1] == ["S001"]

            # vector_results는 hydrated 원본 행
            assert result["vector_results"][0]["service_status"] == "접수중"


class TestHydrationDataFreshness:
    """임베딩 시점의 stale metadata 대신 원본 최신 값이 답변 컨텍스트로 들어가는지 검증."""

    async def test_hydrated_status_overrides_stale_embedding_metadata(self):
        """임베딩 metadata는 '접수마감', 원본은 '접수중'이면 hydrated 값('접수중')이 반환된다."""
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

        # 임베딩 metadata는 stale: '예약마감'
        stale_vector_rows = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "metadata": {"service_status": "예약마감"},
            "similarity": 0.9,
        }]
        # 원본은 최신: '접수중'
        fresh_hydrated = [{
            "service_id": "S001",
            "service_name": "마포 수영장",
            "service_status": "접수중",
        }]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=stale_vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=fresh_hydrated)),
        ):
            state = make_agent_state(message="수영장")
            result = await agent.search(state, MagicMock(), MagicMock())

            # 답변 컨텍스트에는 최신 '접수중'만 들어간다
            assert result["vector_results"][0]["service_status"] == "접수중"
            # stale metadata는 노출되지 않는다
            assert "metadata" not in result["vector_results"][0] or \
                   result["vector_results"][0].get("metadata") is None

    async def test_hydration_drops_rows_missing_in_source(self):
        """임베딩에는 있지만 원본 테이블에 없는 service_id는 vector_results에서 제외된다."""
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

        # 검색 결과: S001과 S002 두 건
        vector_rows = [
            {"service_id": "S001", "service_name": "n1", "metadata": {}, "similarity": 0.9},
            {"service_id": "S002", "service_name": "n2", "metadata": {}, "similarity": 0.8},
        ]
        # 원본에는 S001만 존재 (S002는 soft-delete됨)
        hydrated = [
            {"service_id": "S001", "service_name": "마포 수영장", "service_status": "접수중"},
        ]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services", AsyncMock(return_value=hydrated)),
        ):
            result = await agent.search(make_agent_state(message="수영장"), MagicMock(), MagicMock())

            ids = [r["service_id"] for r in result["vector_results"]]
            assert ids == ["S001"]

    async def test_hydrate_failure_falls_back_to_empty_results(self):
        """hydrate_services가 예외를 던지면 vector_results가 빈 리스트가 된다.

        검색 자체는 성공했으나 hydration이 실패한 경우, stale metadata로 답변하는 것보다
        결과 없음을 안내하는 편이 안전하다. Answer Agent의 _self_correction_edge가
        '결과 없음'을 빈 답변으로 변환하여 재시도 로직이 발동할 수 있다.
        """
        agent = _make_agent("수영장", [0.1] * 768)

        vector_rows = [{"service_id": "S001", "service_name": "n", "metadata": {}, "similarity": 0.9}]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vector_rows)),
            patch("agents.vector_agent.bm25_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.hydrate_services",
                  AsyncMock(side_effect=RuntimeError("DB down"))),
        ):
            result = await agent.search(make_agent_state(message="수영장"), MagicMock(), MagicMock())

            assert result["vector_results"] == []

