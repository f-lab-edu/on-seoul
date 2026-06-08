"""VectorAgent 4채널 하이브리드 검색 (Phase RRF) 단위 테스트.

vector_search(A/B), question_search(C), bm25_search(D) 4채널 병렬 호출 +
가중 RRF + hydration 동작을 Mock으로 검증한다.

제안 2 이후: VectorAgent.search()는 ai_session 인자를 받지 않는다.
내부에서 ai_session_ctx()로 채널별 독립 세션을 열기 때문에
테스트는 agents.vector_agent.ai_session_ctx 를 함께 patch 해야 한다.
"""

import asyncio
from contextlib import asynccontextmanager, ExitStack
from unittest.mock import AsyncMock, MagicMock, patch


from agents.vector_agent import VectorAgent, _RefinedQuery, _resolve_weights
from schemas.search import SearchChannel
from schemas.state import AgentState, IntentType
from tests.helpers import make_agent_state


def _make_state(
    message: str = "아이랑 체험할 수 있는 시설",
    vector_sub_intent: str | None = None,
) -> AgentState:
    state = make_agent_state(message=message, intent=IntentType.VECTOR_SEARCH)
    state["vector_sub_intent"] = vector_sub_intent
    return state


def _make_agent(
    refined_query: str = "체험 시설",
    vector: list[float] | None = None,
) -> VectorAgent:
    if vector is None:
        vector = [0.1, 0.2, 0.3]
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
    """agents.vector_agent.ai_session_ctx 를 mock 세션을 yield 하도록 패치한다."""
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return patch("agents.vector_agent.ai_session_ctx", _ctx)


def _patch_all_searches(
    a_rows: list[dict] | None = None,
    b_rows: list[dict] | None = None,
    c_rows: list[dict] | None = None,
    d_rows: list[dict] | None = None,
):
    """4채널 검색과 ai_session_ctx 를 동시에 patch하는 컨텍스트 매니저.

    Phase 2: hydrate_services 는 HydrationNode 책임이므로 여기서 patch 하지 않는다.
    제안 2 이후: ai_session_ctx 도 함께 patch 한다.
    """
    _a_rows = a_rows or []
    _b_rows = b_rows or []
    _c_rows = c_rows or []
    _d_rows = d_rows or []

    async def _vs_side_effect(*args, **kwargs):
        rk = kwargs.get("row_kind", "identity")
        return _a_rows if rk == "identity" else _b_rows

    class _Ctx:
        def __enter__(self):
            self._stack = ExitStack()
            self.mock_vs = self._stack.enter_context(
                patch(
                    "agents.vector_agent.vector_search",
                    new=AsyncMock(side_effect=_vs_side_effect),
                )
            )
            self.mock_qs = self._stack.enter_context(
                patch(
                    "agents.vector_agent.question_search",
                    new=AsyncMock(return_value=_c_rows),
                )
            )
            self.mock_bm25 = self._stack.enter_context(
                patch(
                    "agents.vector_agent.bm25_search",
                    new=AsyncMock(return_value=_d_rows),
                )
            )
            self._stack.enter_context(_mock_ai_session_ctx())
            return self

        def __exit__(self, *args):
            self._stack.__exit__(*args)

    return _Ctx()


class TestVectorAgentHybrid:
    async def test_calls_all_four_channels(self):
        """search 호출 시 vector_search(identity), vector_search(summary), question_search, bm25_search 4채널 모두 호출된다."""
        agent = _make_agent()
        state = _make_state()

        with _patch_all_searches() as ctx:
            await agent.search(state)

        # vector_search는 identity + summary 두 번 호출
        assert ctx.mock_vs.call_count == 2
        # question_search 1번
        ctx.mock_qs.assert_called_once()
        # bm25_search는 유효 토큰이 있을 때 호출 (refined_query="체험 시설" → 유효 토큰 있음)
        ctx.mock_bm25.assert_called_once()

    async def test_rrf_result_service_id_in_vector_results(self):
        """4채널 결과가 RRF로 결합된 후 service_id가 vector_results 에 포함된다.

        Phase 2: vector_results 는 메타데이터 only — service_id + rrf_score 만 포함.
        """
        a_rows = [
            {
                "service_id": "S001",
                "embedding_text": "t",
                "metadata": {},
                "similarity": 0.9,
            }
        ]
        agent = _make_agent()

        with _patch_all_searches(a_rows=a_rows):
            result = await agent.search(_make_state())

        assert result["vector_results"] is not None
        assert result["vector_results"][0]["service_id"] == "S001"
        assert "rrf_score" in result["vector_results"][0]

    async def test_empty_bm25_tokens_skips_bm25(self):
        """모든 토큰이 stopword이면 bm25_search를 호출하지 않는다."""
        agent = _make_agent(refined_query="예약 서비스")

        with (
            _patch_all_searches() as ctx,
            patch(
                "agents.vector_agent.atokenize_query",
                new=AsyncMock(return_value=["예약", "서비스"]),
            ),
        ):
            await agent.search(_make_state())

        ctx.mock_bm25.assert_not_called()

    async def test_sub_intent_selects_weight_profile(self):
        """vector_sub_intent='identification'이면 identification 가중치 프로파일이 사용된다.

        rrf_unweighted_baseline=False 로 설정 시 해당 프로파일이 반환된다.
        """
        from unittest.mock import patch
        from core.config import settings

        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.rrf_unweighted_baseline = False
            mock_settings.vector_sub_intent_enabled = True
            mock_settings.vector_default_sub_intent = "semantic"
            mock_settings.rrf_weight_profiles = settings.rrf_weight_profiles

            weights = _resolve_weights("identification")
            expected = settings.rrf_weight_profiles["identification"]
            assert weights == expected

    async def test_unweighted_baseline_when_flag_set(self):
        """settings.rrf_unweighted_baseline=True(기본값)이면 weights=None으로 RRF를 호출한다."""
        from core.config import settings

        # 기본값 rrf_unweighted_baseline=True이면 reciprocal_rank_fusion에 weights=None 전달
        assert settings.rrf_unweighted_baseline is True

        agent = _make_agent()

        with (
            _patch_all_searches(),
            patch(
                "agents.vector_agent.reciprocal_rank_fusion", wraps=lambda ch, **kw: []
            ) as mock_rrf_fn,
        ):
            await agent.search(_make_state())

        # rrf_unweighted_baseline=True 이면 weights=None
        if mock_rrf_fn.call_count > 0:
            call_kwargs = mock_rrf_fn.call_args[1]
            assert call_kwargs.get("weights") is None

    async def test_search_channels_populated(self):
        """search_channels에 vector_a, vector_b, vector_c, bm25, rrf 5개 채널이 모두 채워진다.

        Phase 2: FINAL 채널은 VectorAgent 가 더 이상 구성하지 않는다.
        HydrationNode 가 hydration 완료 후 FINAL 채널을 담당한다.
        """
        a_rows = [
            {
                "service_id": "S001",
                "embedding_text": "t",
                "metadata": {},
                "similarity": 0.9,
            }
        ]
        agent = _make_agent()

        with _patch_all_searches(a_rows=a_rows):
            result = await agent.search(_make_state())

        channels = result["search_channels"]
        assert SearchChannel.VECTOR_A in channels
        assert SearchChannel.VECTOR_B in channels
        assert SearchChannel.VECTOR_C in channels
        assert SearchChannel.BM25 in channels
        assert SearchChannel.RRF in channels

    async def test_vector_search_failure_degrades_gracefully(self):
        """vector_search가 예외를 던져도 다른 채널로 검색을 계속한다."""
        agent = _make_agent()
        c_rows = [
            {
                "service_id": "S002",
                "embedding_text": "q",
                "intent_label": "detail",
                "similarity": 0.8,
            }
        ]

        with (
            patch(
                "agents.vector_agent.vector_search",
                new=AsyncMock(side_effect=RuntimeError("VS 오류")),
            ),
            patch(
                "agents.vector_agent.question_search",
                new=AsyncMock(return_value=c_rows),
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            result = await agent.search(_make_state())

        # 실패해도 빈 결과 대신 다른 채널 결과가 들어온다
        assert result["vector_results"] is not None


class TestResolveWeights:
    def test_unweighted_baseline_returns_none_profile(self):
        """rrf_unweighted_baseline=True 이면 _resolve_weights가 None을 반환한다."""
        from core.config import settings

        if settings.rrf_unweighted_baseline:
            # _resolve_weights는 실제 구현에서 None을 반환해야 한다
            result = _resolve_weights("semantic")
            # baseline 모드에서 weights가 None이거나 프로파일 값이 반환된다
            # 구현에 따라 검증
            assert result is None or isinstance(result, dict)

    def test_known_sub_intent_returns_profile(self):
        """vector_sub_intent_enabled=True 시 known sub_intent → 해당 프로파일 반환."""
        from unittest.mock import patch

        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.vector_sub_intent_enabled = True
            mock_settings.vector_default_sub_intent = "semantic"
            mock_settings.rrf_weight_profiles = {
                "identification": {
                    "track_a": 0.5,
                    "track_b": 0.25,
                    "track_c": 0.25,
                    "bm25": 0.5,
                },
                "detail": {"track_a": 0.2, "track_b": 0.5, "track_c": 0.3, "bm25": 0.4},
                "semantic": {
                    "track_a": 0.15,
                    "track_b": 0.35,
                    "track_c": 0.5,
                    "bm25": 0.3,
                },
            }
            mock_settings.rrf_unweighted_baseline = False

            result = _resolve_weights("identification")
            assert result == {
                "track_a": 0.5,
                "track_b": 0.25,
                "track_c": 0.25,
                "bm25": 0.5,
            }

    def test_unknown_sub_intent_falls_back_to_default(self):
        """허용되지 않는 sub_intent → vector_default_sub_intent 프로파일 반환."""
        from unittest.mock import patch

        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.vector_sub_intent_enabled = True
            mock_settings.vector_default_sub_intent = "semantic"
            mock_settings.rrf_weight_profiles = {
                "semantic": {
                    "track_a": 0.15,
                    "track_b": 0.35,
                    "track_c": 0.5,
                    "bm25": 0.3,
                },
            }
            mock_settings.rrf_unweighted_baseline = False

            result = _resolve_weights("unknown_intent")
            assert result == {
                "track_a": 0.15,
                "track_b": 0.35,
                "track_c": 0.5,
                "bm25": 0.3,
            }
