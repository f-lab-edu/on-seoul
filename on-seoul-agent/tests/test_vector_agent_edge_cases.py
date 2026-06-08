"""agents/vector_agent.py 엣지케이스 단위 테스트.

검증 절차 §2 에서 식별된 누락 경로:
  - rrf_unweighted_baseline=True 일 때 reciprocal_rank_fusion에 weights=None 전달
  - 4채널 모두 빈 결과 → 빈 vector_results
  - search_channels에 6개 키 모두 존재 (기존 테스트 보완)
  - _resolve_weights: vector_sub_intent_enabled=False일 때 default 프로파일 사용

제안 2 이후: VectorAgent.search()는 ai_session 인자를 받지 않는다.
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


def _patch_all_empty():
    """4채널 모두 빈 결과 + ai_session_ctx 패치.

    Phase 2: hydrate_services 는 HydrationNode 책임이므로 여기서 patch 하지 않는다.
    제안 2 이후: ai_session_ctx 도 함께 patch 한다.
    """

    class _Ctx:
        def __enter__(self):
            self._stack = ExitStack()
            self._stack.enter_context(
                patch(
                    "agents.vector_agent.vector_search", new=AsyncMock(return_value=[])
                )
            )
            self._stack.enter_context(
                patch(
                    "agents.vector_agent.question_search",
                    new=AsyncMock(return_value=[]),
                )
            )
            self._stack.enter_context(
                patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[]))
            )
            self._stack.enter_context(_mock_ai_session_ctx())
            return self

        def __exit__(self, *args):
            self._stack.__exit__(*args)

    return _Ctx()


class TestVectorAgentWeightPassthrough:
    async def test_unweighted_baseline_true_passes_none_to_rrf(self):
        """rrf_unweighted_baseline=True(기본값)이면 reciprocal_rank_fusion에 weights=None이 전달된다."""
        agent = _make_agent()

        captured_kwargs: list[dict] = []

        def _capture_rrf(channels, **kwargs):
            captured_kwargs.append(kwargs)
            return []

        with (
            _patch_all_empty(),
            patch("agents.vector_agent.settings") as mock_settings,
            patch(
                "agents.vector_agent.reciprocal_rank_fusion", side_effect=_capture_rrf
            ),
        ):
            mock_settings.rrf_unweighted_baseline = True
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
            mock_settings.vector_sub_intent_enabled = False
            mock_settings.vector_default_sub_intent = "semantic"
            mock_settings.vector_channel_concurrency = 4
            mock_settings.rrf_weight_profiles = {
                "semantic": {
                    "track_a": 0.15,
                    "track_b": 0.35,
                    "track_c": 0.5,
                    "bm25": 0.3,
                }
            }

            await agent.search(_make_state())

        assert len(captured_kwargs) == 1, (
            "reciprocal_rank_fusion이 정확히 1번 호출돼야 한다"
        )
        assert captured_kwargs[0].get("weights") is None, (
            f"rrf_unweighted_baseline=True이면 weights=None이어야 하지만 "
            f"{captured_kwargs[0].get('weights')!r} 전달됨"
        )


class TestVectorAgentAllChannelsEmpty:
    async def test_all_channels_empty_returns_empty_vector_results(self):
        """4채널 모두 빈 결과일 때 vector_results는 빈 리스트여야 한다."""
        agent = _make_agent()

        with _patch_all_empty():
            result = await agent.search(_make_state())

        assert result["vector_results"] == [], (
            f"4채널 모두 빈 결과이면 vector_results=[] 이어야 하지만 {result['vector_results']!r}"
        )

    async def test_all_channels_empty_search_channels_has_rrf_key(self):
        """4채널 모두 빈 결과여도 search_channels에 core 채널 키가 존재해야 한다.

        Phase 2: VectorAgent 는 FINAL 채널을 구성하지 않는다 (HydrationNode 책임).
        VECTOR_A/B/C, BM25, RRF 5개 채널이 항상 포함된다.
        """
        agent = _make_agent()

        with _patch_all_empty():
            result = await agent.search(_make_state())

        channels = result["search_channels"]
        for key in (
            SearchChannel.VECTOR_A,
            SearchChannel.VECTOR_B,
            SearchChannel.VECTOR_C,
            SearchChannel.BM25,
            SearchChannel.RRF,
        ):
            assert key in channels, f"search_channels에 누락된 키: {key}"


class TestResolveWeightsEdgeCases:
    def test_sub_intent_enabled_false_uses_default_profile(self):
        """vector_sub_intent_enabled=False이면 sub_intent 값에 관계없이 default 프로파일을 사용한다."""
        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.rrf_unweighted_baseline = False
            mock_settings.vector_sub_intent_enabled = False
            mock_settings.vector_default_sub_intent = "semantic"
            mock_settings.rrf_weight_profiles = {
                "identification": {
                    "track_a": 0.5,
                    "track_b": 0.25,
                    "track_c": 0.25,
                    "bm25": 0.5,
                },
                "semantic": {
                    "track_a": 0.15,
                    "track_b": 0.35,
                    "track_c": 0.5,
                    "bm25": 0.3,
                },
            }

            # sub_intent가 'identification'이어도 enabled=False이면 default 'semantic' 반환
            result = _resolve_weights("identification")
            assert result == {
                "track_a": 0.15,
                "track_b": 0.35,
                "track_c": 0.5,
                "bm25": 0.3,
            }, (
                "vector_sub_intent_enabled=False이면 sub_intent를 무시하고 default 프로파일을 써야 한다"
            )

    def test_sub_intent_none_uses_default_profile(self):
        """sub_intent=None이면 vector_default_sub_intent 프로파일을 사용한다."""
        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.rrf_unweighted_baseline = False
            mock_settings.vector_sub_intent_enabled = True
            mock_settings.vector_default_sub_intent = "detail"
            mock_settings.rrf_weight_profiles = {
                "detail": {"track_a": 0.2, "track_b": 0.5, "track_c": 0.3, "bm25": 0.4},
            }

            result = _resolve_weights(None)
            assert result == {
                "track_a": 0.2,
                "track_b": 0.5,
                "track_c": 0.3,
                "bm25": 0.4,
            }

    def test_unknown_sub_intent_falls_back_to_default_not_error(self):
        """허용되지 않는 sub_intent는 KeyError 없이 default 프로파일로 폴백한다."""
        with patch("agents.vector_agent.settings") as mock_settings:
            mock_settings.rrf_unweighted_baseline = False
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

            # 예외 없이 실행되어야 한다
            result = _resolve_weights("totally_invalid_intent")
            assert result == {
                "track_a": 0.15,
                "track_b": 0.35,
                "track_c": 0.5,
                "bm25": 0.3,
            }


class TestIntentOutputValidation:
    def test_invalid_vector_sub_intent_string_is_normalized_to_none(self):
        """_IntentOutput에 허용되지 않는 vector_sub_intent 문자열을 직접 전달하면 None으로 정규화된다."""
        from agents.router_agent import _IntentOutput
        from schemas.state import IntentType

        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="not_a_valid_intent",  # type: ignore[arg-type]
        )
        assert output.vector_sub_intent is None, (
            f"허용되지 않는 값은 None으로 정규화돼야 하지만 {output.vector_sub_intent!r}"
        )

    def test_integer_vector_sub_intent_is_normalized_to_none(self):
        """_IntentOutput에 정수를 vector_sub_intent로 전달하면 None으로 정규화된다."""
        from agents.router_agent import _IntentOutput
        from schemas.state import IntentType

        output = _IntentOutput(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent=42,  # type: ignore[arg-type]
        )
        assert output.vector_sub_intent is None

    def test_sql_search_intent_vector_sub_intent_is_none(self):
        """SQL_SEARCH 의도이면 vector_sub_intent는 None이다."""
        from agents.router_agent import _IntentOutput
        from schemas.state import IntentType

        output = _IntentOutput(
            intent=IntentType.SQL_SEARCH,
            refined_query="마포구 수영장",
        )
        assert output.vector_sub_intent is None
