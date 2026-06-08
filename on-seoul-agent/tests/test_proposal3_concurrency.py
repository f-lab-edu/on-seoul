"""제안 3 — AI 서비스 동시성 개선 테스트.

검증 항목:
1. httpx Limits 설정값 — llm/client.py _make_httpx_limits() 반환값 단언
2. OpenAI ChatOpenAI 생성 시 async_client에 커스텀 httpx.AsyncClient 주입 확인
3. 글로벌 fan-out 세마포어 상한 — init_global_sema() 후 vector_global_sema._value 단언
4. _run_channel 글로벌 세마포어 + 채널 세마포어 중첩 동작
5. atokenize_query — asyncio.to_thread를 경유하는 비동기 래퍼 검증
6. 토크나이저 블로킹 오프로드 — 이벤트 루프 블로킹 없이 실행 완료
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import core.concurrency as _concurrency
from core.concurrency import init_global_sema
from llm.client import _make_httpx_limits


# ---------------------------------------------------------------------------
# 1. httpx Limits 설정값 검증
# ---------------------------------------------------------------------------


class TestHttpxLimits:
    def test_max_connections_matches_config(self):
        """_make_httpx_limits(N).max_connections == N."""
        limits = _make_httpx_limits(400)
        assert limits.max_connections == 400

    def test_max_keepalive_connections_fixed(self):
        """max_keepalive_connections는 100으로 고정된다."""
        limits = _make_httpx_limits(200)
        assert limits.max_keepalive_connections == 100

    def test_keepalive_expiry_fixed(self):
        """keepalive_expiry는 30초로 고정된다."""
        limits = _make_httpx_limits(400)
        assert limits.keepalive_expiry == 30

    def test_returns_httpx_limits_instance(self):
        """반환 타입이 httpx.Limits이다."""
        limits = _make_httpx_limits(100)
        assert isinstance(limits, httpx.Limits)

    def test_embedding_http_max_connections_setting_present(self):
        """settings에 embedding_http_max_connections가 존재하고 양수이다."""
        from core.config import settings

        assert hasattr(settings, "embedding_http_max_connections")
        assert settings.embedding_http_max_connections > 0

    def test_llm_http_max_connections_setting_present(self):
        """settings에 llm_http_max_connections가 존재하고 양수이다."""
        from core.config import settings

        assert hasattr(settings, "llm_http_max_connections")
        assert settings.llm_http_max_connections > 0

    def test_llm_http_max_connections_default_is_400(self):
        """llm_http_max_connections 기본값은 400이다."""
        from core.config import settings

        assert settings.llm_http_max_connections == 400

    def test_embedding_http_max_connections_default_is_200(self):
        """embedding_http_max_connections 기본값은 200이다."""
        from core.config import settings

        assert settings.embedding_http_max_connections == 200


# ---------------------------------------------------------------------------
# 2. OpenAI ChatOpenAI async_client 주입 확인
# ---------------------------------------------------------------------------


class TestOpenAIHttpxClientInjection:
    def test_openai_get_chat_model_passes_async_client(self):
        """get_chat_model(provider='openai')가 ChatOpenAI에 async_client를 전달한다."""
        from llm.client import get_chat_model

        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_cls,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            get_chat_model(provider="openai")

            call_kwargs = mock_cls.call_args.kwargs
            assert "async_client" in call_kwargs
            assert isinstance(call_kwargs["async_client"], httpx.AsyncClient)

    def test_openai_async_client_limits_match_settings(self):
        """ChatOpenAI에 전달되는 async_client 생성 시 llm_http_max_connections 값으로 Limits가 전달된다."""
        from llm.client import get_chat_model

        created_limits: list[httpx.Limits] = []

        original_async_client = httpx.AsyncClient

        def _capturing_async_client(*args, **kwargs):
            if "limits" in kwargs:
                created_limits.append(kwargs["limits"])
            return original_async_client(*args, **kwargs)

        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI"),
            patch("llm.client.httpx.AsyncClient", side_effect=_capturing_async_client),
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            get_chat_model(provider="openai")

        assert len(created_limits) == 1
        assert created_limits[0].max_connections == 400

    def test_gemini_get_chat_model_does_not_pass_async_client(self):
        """Gemini는 httpx.AsyncClient 주입을 지원하지 않으므로 async_client를 전달하지 않는다."""
        from llm.client import get_chat_model

        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"

            get_chat_model(provider="gemini")

            call_kwargs = mock_cls.call_args.kwargs
            assert "async_client" not in call_kwargs


# ---------------------------------------------------------------------------
# 3. 글로벌 fan-out 세마포어 상한 단언
# ---------------------------------------------------------------------------


class TestGlobalSemaphore:
    def setup_method(self):
        """각 테스트 전 전역 세마포어를 None으로 초기화한다."""
        _concurrency.vector_global_sema = None

    def teardown_method(self):
        """테스트 후 전역 세마포어를 None으로 복원한다."""
        _concurrency.vector_global_sema = None

    def test_vector_global_concurrency_setting_present(self):
        """settings에 vector_global_concurrency가 존재하고 양수이다."""
        from core.config import settings

        assert hasattr(settings, "vector_global_concurrency")
        assert settings.vector_global_concurrency > 0

    def test_vector_global_concurrency_default_is_20(self):
        """vector_global_concurrency 기본값은 20이다."""
        from core.config import settings

        assert settings.vector_global_concurrency == 20

    def test_init_global_sema_sets_semaphore(self):
        """init_global_sema() 호출 후 vector_global_sema가 None이 아니다."""
        init_global_sema(concurrency=20)
        assert _concurrency.vector_global_sema is not None

    def test_init_global_sema_value_matches_concurrency(self):
        """init_global_sema(concurrency=N)으로 생성된 세마포어의 초기 값이 N이다."""
        sema = init_global_sema(concurrency=20)
        assert sema._value == 20

    def test_init_global_sema_custom_concurrency(self):
        """임의의 concurrency 값을 전달하면 해당 값으로 세마포어가 초기화된다."""
        sema = init_global_sema(concurrency=5)
        assert sema._value == 5

    def test_init_global_sema_uses_settings_when_none(self):
        """concurrency=None이면 settings.vector_global_concurrency를 사용한다."""
        from core.config import settings

        sema = init_global_sema(concurrency=None)
        assert sema._value == settings.vector_global_concurrency

    def test_init_global_sema_returns_asyncio_semaphore(self):
        """init_global_sema()는 asyncio.Semaphore를 반환한다."""
        sema = init_global_sema(concurrency=10)
        assert isinstance(sema, asyncio.Semaphore)


# ---------------------------------------------------------------------------
# 4. _run_channel 글로벌 세마포어 + 채널 세마포어 중첩 동작
# ---------------------------------------------------------------------------


def _make_vector_agent(channel_concurrency: int = 4) -> object:
    """VectorAgent 인스턴스를 LLM/임베딩 없이 생성한다."""
    from agents.vector_agent import VectorAgent, _RefinedQuery

    agent = VectorAgent.__new__(VectorAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_RefinedQuery(refined_query="테스트"))
    agent._refine_chain = chain
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    agent._embeddings = emb
    agent._channel_sema = asyncio.Semaphore(channel_concurrency)
    return agent


def _mock_ai_session_ctx():
    """ai_session_ctx를 mock 세션 yield로 패치한다."""
    mock_session = MagicMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    return patch("agents.vector_agent.ai_session_ctx", _ctx)


class TestGlobalSemaphoreInRunChannel:
    def setup_method(self):
        _concurrency.vector_global_sema = None

    def teardown_method(self):
        _concurrency.vector_global_sema = None

    async def test_global_sema_none_runs_without_error(self):
        """글로벌 세마포어가 None이면(lifespan 전) 채널 세마포어만으로 정상 실행된다."""
        agent = _make_vector_agent()

        with (
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            from schemas.state import IntentType
            from tests.helpers import make_agent_state

            state = make_agent_state(message="테스트", intent=IntentType.VECTOR_SEARCH)
            result = await agent.search(state)

        assert result["vector_results"] is not None

    async def test_global_sema_limits_total_concurrent_channels(self):
        """글로벌 세마포어(cap=2)가 채널(4)보다 작을 때 동시 실행 수가 cap 이하이다."""
        # 글로벌 세마포어를 2로 초기화
        init_global_sema(concurrency=2)

        concurrent_peak: list[int] = []
        active = {"count": 0}

        async def _slow_vs(*args, **kwargs):
            active["count"] += 1
            concurrent_peak.append(active["count"])
            await asyncio.sleep(0)
            active["count"] -= 1
            return []

        async def _slow_qs(*args, **kwargs):
            active["count"] += 1
            concurrent_peak.append(active["count"])
            await asyncio.sleep(0)
            active["count"] -= 1
            return []

        async def _slow_bm25(*args, **kwargs):
            active["count"] += 1
            concurrent_peak.append(active["count"])
            await asyncio.sleep(0)
            active["count"] -= 1
            return []

        # channel_concurrency=4(기본)지만 global sema=2이므로 동시 실행은 최대 2
        agent = _make_vector_agent(channel_concurrency=4)

        with (
            patch(
                "agents.vector_agent.vector_search", new=AsyncMock(side_effect=_slow_vs)
            ),
            patch(
                "agents.vector_agent.question_search",
                new=AsyncMock(side_effect=_slow_qs),
            ),
            patch(
                "agents.vector_agent.bm25_search",
                new=AsyncMock(side_effect=_slow_bm25),
            ),
            _mock_ai_session_ctx(),
        ):
            from schemas.state import IntentType
            from tests.helpers import make_agent_state

            state = make_agent_state(message="테스트", intent=IntentType.VECTOR_SEARCH)
            await agent.search(state)

        # 글로벌 세마포어(2)가 외곽 가드이므로 최대 동시 실행 수 ≤ 2
        assert max(concurrent_peak) <= 2

    async def test_global_sema_set_allows_up_to_cap(self):
        """글로벌 세마포어(cap=4)이면 4채널이 동시에 실행 가능하다."""
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

        agent = _make_vector_agent(channel_concurrency=4)

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
            from schemas.state import IntentType
            from tests.helpers import make_agent_state

            state = make_agent_state(message="테스트", intent=IntentType.VECTOR_SEARCH)
            await asyncio.wait_for(agent.search(state), timeout=5.0)

        assert len(reached_barrier) == 4


# ---------------------------------------------------------------------------
# 5 & 6. atokenize_query — asyncio.to_thread 경유 비동기 래퍼
# ---------------------------------------------------------------------------


class TestAtokenizeQuery:
    async def test_atokenize_query_returns_list(self):
        """atokenize_query는 list[str]을 반환한다."""
        from tools.tokenizer import atokenize_query

        with patch("tools.tokenizer.tokenize_query", return_value=["수영", "강습"]):
            result = await atokenize_query("수영 강습")

        assert isinstance(result, list)
        assert result == ["수영", "강습"]

    async def test_atokenize_query_calls_tokenize_query(self):
        """atokenize_query는 내부적으로 tokenize_query를 호출한다."""
        from tools.tokenizer import atokenize_query

        called_with: list[str] = []

        def _fake_tokenize(text: str) -> list[str]:
            called_with.append(text)
            return ["테스트"]

        with patch("tools.tokenizer.tokenize_query", side_effect=_fake_tokenize):
            await atokenize_query("테스트 질의")

        assert called_with == ["테스트 질의"]

    async def test_atokenize_query_empty_string(self):
        """빈 문자열 입력 시 빈 리스트를 반환한다."""
        from tools.tokenizer import atokenize_query

        with patch("tools.tokenizer.tokenize_query", return_value=[]):
            result = await atokenize_query("")

        assert result == []

    async def test_atokenize_query_uses_to_thread(self):
        """atokenize_query는 asyncio.to_thread를 사용하여 블로킹 함수를 오프로드한다."""
        from tools.tokenizer import atokenize_query

        to_thread_calls: list = []
        original_to_thread = asyncio.to_thread

        async def _spy_to_thread(fn, *args, **kwargs):
            to_thread_calls.append(fn)
            return await original_to_thread(fn, *args, **kwargs)

        with (
            patch("tools.tokenizer.tokenize_query", return_value=["수영"]),
            patch("tools.tokenizer.asyncio.to_thread", side_effect=_spy_to_thread),
        ):
            await atokenize_query("수영")

        assert len(to_thread_calls) == 1

    async def test_vector_agent_search_uses_atokenize_query(self):
        """VectorAgent.search()가 atokenize_query를 호출한다(동기 tokenize_query 미사용)."""
        agent = _make_vector_agent()
        atokenize_calls: list[str] = []

        async def _fake_atokenize(text: str) -> list[str]:
            atokenize_calls.append(text)
            return ["수영"]

        with (
            patch(
                "agents.vector_agent.atokenize_query", side_effect=_fake_atokenize
            ),
            patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])),
            patch(
                "agents.vector_agent.question_search", new=AsyncMock(return_value=[])
            ),
            patch("agents.vector_agent.bm25_search", new=AsyncMock(return_value=[])),
            _mock_ai_session_ctx(),
        ):
            from schemas.state import IntentType
            from tests.helpers import make_agent_state

            state = make_agent_state(
                message="수영 강습", intent=IntentType.VECTOR_SEARCH
            )
            await agent.search(state)

        assert len(atokenize_calls) == 1

    async def test_atokenize_query_propagates_exception(self):
        """tokenize_query가 예외를 일으키면 atokenize_query가 그대로 전파한다."""
        from tools.tokenizer import atokenize_query

        def _raise(_text: str) -> list[str]:
            raise RuntimeError("tokenizer failure")

        with patch("tools.tokenizer.tokenize_query", side_effect=_raise):
            try:
                await atokenize_query("오류 발생")
                raise AssertionError("예외가 전파되지 않았습니다")
            except RuntimeError as exc:
                assert "tokenizer failure" in str(exc)


# ---------------------------------------------------------------------------
# 7. lifespan — init_global_sema가 모듈 전역 변수를 채우는지 확인
# ---------------------------------------------------------------------------


class TestLifespanInitGlobalSema:
    def setup_method(self):
        _concurrency.vector_global_sema = None

    def teardown_method(self):
        _concurrency.vector_global_sema = None

    def test_init_global_sema_populates_module_global(self):
        """init_global_sema() 호출 후 core.concurrency.vector_global_sema가 설정된다.

        main.py lifespan은 init_global_sema()를 호출한다. 이 테스트는 해당 호출이
        모듈 전역 변수에 세마포어를 등록하는지 직접 단언한다.
        """
        assert _concurrency.vector_global_sema is None

        init_global_sema(concurrency=20)

        assert _concurrency.vector_global_sema is not None
        assert isinstance(_concurrency.vector_global_sema, asyncio.Semaphore)
        assert _concurrency.vector_global_sema._value == 20

    async def test_lifespan_calls_init_global_sema(self):
        """main.py lifespan이 init_global_sema()를 호출하여 vector_global_sema를 초기화한다."""
        import core.concurrency as concurrency_module

        with patch("main.init_global_sema", wraps=concurrency_module.init_global_sema) as mock_init:
            from main import lifespan, app as fastapi_app

            with (
                patch("main.get_redis") as mock_redis,
                patch("main.AgentGraph"),
                patch("main.setup_telemetry"),
                patch("main.shutdown_telemetry"),
            ):
                mock_redis_inst = MagicMock()
                mock_redis_inst.aclose = AsyncMock()
                mock_redis.return_value = mock_redis_inst

                async with lifespan(fastapi_app):
                    pass

        mock_init.assert_called_once()
