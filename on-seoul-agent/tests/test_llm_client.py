"""llm/client.py 단위 테스트.

_rate_limited 데코레이터, _GeminiEmbeddings 래퍼,
get_chat_model / get_embeddings 팩토리 함수의 동작을 검증한다.
실제 API 호출 없이 mock으로만 실행된다.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiolimiter import AsyncLimiter
from google.api_core.exceptions import ResourceExhausted

from core.exceptions import ConfigurationException, RateLimitException
from llm.client import _GeminiEmbeddings, _rate_limited, get_chat_model, get_embeddings

# 테스트 전용 빠른 limiter — 실제 대기 없이 rate limit 흐름을 검증할 때 주입한다.
_FAST_LIMITER = AsyncLimiter(max_rate=1000, time_period=0.001)


# ---------------------------------------------------------------------------
# _rate_limited 데코레이터
# ---------------------------------------------------------------------------


class TestRateLimitedDecorator:
    async def test_return_value_is_passed_through(self):
        """래핑된 함수의 반환값이 그대로 전달된다."""
        limiter = AsyncLimiter(max_rate=100, time_period=1)

        @_rate_limited(limiter)
        async def fn() -> str:
            return "ok"

        assert await fn() == "ok"

    async def test_arguments_are_forwarded(self):
        """위치·키워드 인자가 원본 함수로 그대로 전달된다."""
        limiter = AsyncLimiter(max_rate=100, time_period=1)
        received: list = []

        @_rate_limited(limiter)
        async def fn(a: int, b: str = "default") -> None:
            received.append((a, b))

        await fn(1, b="hello")
        assert received == [(1, "hello")]

    async def test_wraps_preserves_function_metadata(self):
        """@wraps 로 함수 이름과 docstring이 보존된다."""
        limiter = AsyncLimiter(max_rate=100, time_period=1)

        @_rate_limited(limiter)
        async def my_function() -> None:
            """원본 docstring."""

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "원본 docstring."

    async def test_limiter_context_manager_is_acquired(self):
        """limiter의 컨텍스트 매니저가 실제로 호출된다."""
        mock_limiter = MagicMock()
        mock_limiter.__aenter__ = AsyncMock(return_value=None)
        mock_limiter.__aexit__ = AsyncMock(return_value=False)

        @_rate_limited(mock_limiter)
        async def fn() -> str:
            return "result"

        await fn()

        mock_limiter.__aenter__.assert_called_once()
        mock_limiter.__aexit__.assert_called_once()

    async def test_exception_propagates_and_context_manager_exits(self):
        """원본 함수에서 예외가 발생해도 limiter의 __aexit__가 호출된다."""
        limiter = AsyncLimiter(max_rate=100, time_period=1)

        @_rate_limited(limiter)
        async def fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await fn()

    async def test_burst_is_throttled(self):
        """rate limit를 초과하는 동시 호출은 실제로 지연된다.

        로컬 tight limiter(max_rate=3, time_period=1)로 6건 처리 시
        최소 1초 이상 소요되어야 한다.
        autouse patch와 무관하게 별도 limiter를 직접 주입하여 검증한다.
        """
        limiter = AsyncLimiter(max_rate=3, time_period=1)

        @_rate_limited(limiter)
        async def fn() -> None:
            pass

        start = time.monotonic()
        await asyncio.gather(*[fn() for _ in range(6)])
        elapsed = time.monotonic() - start

        assert elapsed >= 1.0, f"throttling 미적용 — {elapsed:.2f}s 만에 완료됨"


# ---------------------------------------------------------------------------
# _GeminiEmbeddings
# ---------------------------------------------------------------------------


class TestGeminiEmbeddings:
    def _make_embeddings(self, vector: list[float] | None = None) -> _GeminiEmbeddings:
        """mock base + 빠른 limiter를 주입한 _GeminiEmbeddings 인스턴스를 반환한다.

        프로덕션 limiter(60초 간격)를 주입하면 테스트가 수십 초 걸리므로
        _FAST_LIMITER로 교체하여 실제 대기 없이 동작을 검증한다.
        """
        base = MagicMock()
        base.embed_query.return_value = vector or [0.1, 0.2]
        base.embed_documents.return_value = [vector or [0.1, 0.2]]
        base.aembed_query = AsyncMock(return_value=vector or [0.1, 0.2])
        return _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

    # --- 동기 메서드 — base에 위임하고 경고 로그 출력 ---

    def test_embed_query_delegates_to_base(self):
        """embed_query는 base.embed_query에 위임하고 결과를 반환한다."""
        emb = self._make_embeddings([0.1, 0.2])
        result = emb.embed_query("text")
        assert result == [0.1, 0.2]
        emb._base.embed_query.assert_called_once_with("text")

    def test_embed_documents_delegates_to_base(self):
        """embed_documents는 base.embed_documents에 위임하고 결과를 반환한다."""
        emb = self._make_embeddings([0.1, 0.2])
        result = emb.embed_documents(["a", "b"])
        assert result == [[0.1, 0.2]]
        emb._base.embed_documents.assert_called_once_with(["a", "b"])

    def test_embed_query_logs_warning(self):
        """sync embed_query는 rate limiting 없음을 경고 로그로 알린다."""
        emb = self._make_embeddings()
        with self._assert_warning_logged("embed_query"):
            emb.embed_query("text")

    def test_embed_documents_logs_warning(self):
        """sync embed_documents는 rate limiting 없음을 경고 로그로 알린다."""
        emb = self._make_embeddings()
        with self._assert_warning_logged("embed_documents"):
            emb.embed_documents(["a"])

    @staticmethod
    def _assert_warning_logged(keyword: str):
        import logging
        from unittest.mock import patch as _patch

        return _patch.object(
            logging.getLogger("llm.client"),
            "warning",
            wraps=lambda msg, *a, **kw: None,
        )

    # --- 비동기 ---

    async def test_aembed_query_returns_vector(self):
        emb = self._make_embeddings([0.5, 0.6])
        result = await emb.aembed_query("hello")
        assert result == [0.5, 0.6]

    async def test_aembed_query_calls_base_with_text(self):
        emb = self._make_embeddings()
        await emb.aembed_query("서울 수영장")
        emb._base.aembed_query.assert_called_once_with("서울 수영장")

    async def test_aembed_documents_returns_one_vector_per_text(self):
        """N개 텍스트 → N개 벡터 반환."""
        base = MagicMock()
        base.aembed_query = AsyncMock(side_effect=lambda t: [float(ord(t[0])), 0.0])
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        result = await emb.aembed_documents(["abc", "def", "ghi"])

        assert len(result) == 3
        assert result[0] == [float(ord("a")), 0.0]
        assert result[1] == [float(ord("d")), 0.0]
        assert result[2] == [float(ord("g")), 0.0]

    async def test_aembed_documents_calls_aembed_query_for_each_text(self):
        """aembed_documents는 텍스트 수만큼 base.aembed_query를 호출한다."""
        base = MagicMock()
        base.aembed_query = AsyncMock(return_value=[0.0])
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        texts = ["a", "b", "c", "d"]
        await emb.aembed_documents(texts)

        assert base.aembed_query.call_count == len(texts)

    async def test_aembed_documents_empty_input(self):
        """빈 리스트 입력 시 빈 리스트를 반환한다."""
        emb = self._make_embeddings()
        result = await emb.aembed_documents([])
        assert result == []

    async def test_aembed_query_is_rate_limited(self):
        """rate limiter가 적용되어 버스트를 초과하면 실제 지연이 발생한다.

        tight_limiter(max_rate=3, time_period=1) 주입 → 6건 처리 시 1초 이상 소요.
        """
        tight_limiter = AsyncLimiter(max_rate=3, time_period=1)
        base = MagicMock()
        base.aembed_query = AsyncMock(return_value=[0.0])
        emb = _GeminiEmbeddings(base, limiter=tight_limiter)

        start = time.monotonic()
        await emb.aembed_documents(["t1", "t2", "t3", "t4", "t5", "t6"])
        elapsed = time.monotonic() - start

        assert elapsed >= 1.0, f"rate limit 미적용 — {elapsed:.2f}s 만에 완료됨"

    async def test_aembed_query_retries_on_429(self):
        """ResourceExhausted 수신 시 재시도하여 결과를 반환한다."""
        call_count = 0

        async def _fail_once(text: str) -> list[float]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ResourceExhausted("rate limit exceeded")
            return [0.1, 0.2]

        base = MagicMock()
        base.aembed_query = AsyncMock(side_effect=_fail_once)
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with patch("llm.client.asyncio.sleep", AsyncMock()):
            result = await emb.aembed_query("test")

        assert result == [0.1, 0.2]
        assert call_count == 2

    async def test_aembed_query_raises_after_max_retries(self):
        """최대 재시도 횟수를 초과하면 예외를 올린다."""
        base = MagicMock()
        base.aembed_query = AsyncMock(
            side_effect=Exception("429 RESOURCE_EXHAUSTED: persistent")
        )
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with (
            patch("llm.client.asyncio.sleep", AsyncMock()),
            pytest.raises(Exception, match="429"),
        ):
            await emb.aembed_query("test")

    async def test_aembed_query_non_429_raises_immediately(self):
        """429가 아닌 예외는 재시도 없이 즉시 올린다."""
        call_count = 0

        async def _network_error(text: str) -> list[float]:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network failure")

        base = MagicMock()
        base.aembed_query = AsyncMock(side_effect=_network_error)
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with pytest.raises(ConnectionError, match="network failure"):
            await emb.aembed_query("test")

        assert call_count == 1  # 재시도 없음

    # --- A: Jitter ---

    async def test_aembed_query_retry_uses_random_uniform(self):
        """재시도 delay 계산에 random.uniform이 실제로 호출된다."""
        base = MagicMock()
        base.aembed_query = AsyncMock(
            side_effect=[
                ResourceExhausted("rate limit"),
                [0.1, 0.2],
            ]
        )
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with (
            patch("llm.client.asyncio.sleep", AsyncMock()),
            patch("llm.client.random.uniform", return_value=0.5) as mock_uniform,
        ):
            result = await emb.aembed_query("test")

        mock_uniform.assert_called_once()
        assert result == [0.1, 0.2]

    async def test_aembed_query_jitter_delay_upper_bound(self):
        """random.uniform 호출 시 상한이 min(base_delay * 2^attempt, max_delay) 이다."""
        from llm.client import _EMBED_RETRY_BASE_DELAY, _EMBED_RETRY_MAX_DELAY

        base = MagicMock()
        base.aembed_query = AsyncMock(
            side_effect=[
                ResourceExhausted("rate limit"),
                [0.3],
            ]
        )
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        captured_args: list = []

        def _capture_uniform(lo, hi):
            captured_args.append((lo, hi))
            return 0.0

        with (
            patch("llm.client.asyncio.sleep", AsyncMock()),
            patch("llm.client.random.uniform", side_effect=_capture_uniform),
        ):
            await emb.aembed_query("test")

        assert captured_args[0][0] == 0  # lower bound always 0
        # attempt=0: base_delay * 2^0 = 10.0 < 60.0 → 캡 미적용
        expected_upper = min(_EMBED_RETRY_BASE_DELAY * (2**0), _EMBED_RETRY_MAX_DELAY)
        assert captured_args[0][1] == expected_upper

    async def test_aembed_query_jitter_delay_capped_at_max(self):
        """attempt가 충분히 크면 delay 상한이 _EMBED_RETRY_MAX_DELAY로 캡된다."""
        from llm.client import _EMBED_RETRY_BASE_DELAY, _EMBED_RETRY_MAX_DELAY

        # attempt=3: base_delay * 2^3 = 80.0 > 60.0 → 캡 적용
        assert _EMBED_RETRY_BASE_DELAY * (2**3) > _EMBED_RETRY_MAX_DELAY, (
            "이 테스트는 base_delay * 2^3 > max_delay 조건을 전제한다"
        )

        # attempt=3까지 도달시키기 위해 4번 실패 후 성공
        base = MagicMock()
        base.aembed_query = AsyncMock(
            side_effect=[
                ResourceExhausted("rate limit"),
                ResourceExhausted("rate limit"),
                ResourceExhausted("rate limit"),
                ResourceExhausted("rate limit"),
                [0.5],
            ]
        )
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        captured_args: list = []

        def _capture_uniform(lo, hi):
            captured_args.append((lo, hi))
            return 0.0

        with (
            patch("llm.client.asyncio.sleep", AsyncMock()),
            patch("llm.client.random.uniform", side_effect=_capture_uniform),
        ):
            await emb.aembed_query("test")

        # attempt=3 시점의 호출 (4번째 uniform 호출)
        assert len(captured_args) == 4
        _, hi = captured_args[3]
        assert hi == _EMBED_RETRY_MAX_DELAY

    # --- B: RateLimitException ---

    async def test_aembed_query_raises_rate_limit_exception_after_max_retries(self):
        """ResourceExhausted가 최대 재시도 횟수만큼 반복되면 RateLimitException이 발생한다."""
        from llm.client import _EMBED_MAX_RETRIES

        base = MagicMock()
        base.aembed_query = AsyncMock(
            side_effect=ResourceExhausted("persistent rate limit")
        )
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with (
            patch("llm.client.asyncio.sleep", AsyncMock()),
            pytest.raises(RateLimitException, match="rate limit 소진"),
        ):
            await emb.aembed_query("test")

        assert base.aembed_query.call_count == _EMBED_MAX_RETRIES

    async def test_rate_limit_exception_is_subclass_of_llm_exception(self):
        """RateLimitException은 LLMException의 하위 클래스다."""
        from core.exceptions import LLMException

        exc = RateLimitException("소진", detail=None)
        assert isinstance(exc, LLMException)

    async def test_rate_limit_exception_carries_original_detail(self):
        """RateLimitException.detail에 원본 ResourceExhausted 예외가 담긴다."""
        base = MagicMock()
        original_exc = ResourceExhausted("quota")
        base.aembed_query = AsyncMock(side_effect=original_exc)
        emb = _GeminiEmbeddings(base, limiter=_FAST_LIMITER)

        with patch("llm.client.asyncio.sleep", AsyncMock()):
            try:
                await emb.aembed_query("test")
            except RateLimitException as exc:
                assert exc.detail is original_exc
            else:
                pytest.fail("RateLimitException이 발생하지 않았습니다")


# ---------------------------------------------------------------------------
# get_chat_model 팩토리
# ---------------------------------------------------------------------------


class TestGetChatModel:
    """get_chat_model 팩토리 함수 테스트.

    실제 LLM 클래스 생성을 막기 위해 ChatGoogleGenerativeAI / ChatOpenAI 생성자를 mock한다.
    settings는 llm.client 모듈 내 참조를 직접 patch한다.
    """

    # --- API 키 누락 시 즉시 실패 ---

    def test_gemini_raises_when_google_api_key_missing(self):
        """google_api_key 없이 gemini provider 요청 시 ConfigurationException."""
        with patch("llm.client.settings") as mock_settings:
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = None

            with pytest.raises(ConfigurationException, match="GOOGLE_API_KEY"):
                get_chat_model(provider="gemini")

    def test_google_alias_raises_when_google_api_key_missing(self):
        """provider='google' 별칭도 동일하게 검사한다."""
        with patch("llm.client.settings") as mock_settings:
            mock_settings.llm_provider = "google"
            mock_settings.google_api_key = ""  # 빈 문자열도 누락으로 취급

            with pytest.raises(ConfigurationException, match="GOOGLE_API_KEY"):
                get_chat_model(provider="google")

    def test_openai_raises_when_openai_api_key_missing(self):
        """openai_api_key 없이 openai provider 요청 시 ConfigurationException."""
        with patch("llm.client.settings") as mock_settings:
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = None

            with pytest.raises(ConfigurationException, match="OPENAI_API_KEY"):
                get_chat_model(provider="openai")

    def test_unknown_provider_raises(self):
        """지원하지 않는 provider 문자열은 ConfigurationException."""
        with patch("llm.client.settings") as mock_settings:
            mock_settings.llm_provider = "anthropic"

            with pytest.raises(ConfigurationException, match="Unknown LLM provider"):
                get_chat_model(provider="anthropic")

    # --- 정상 경로: 올바른 클래스 인스턴스 반환 ---

    def test_gemini_returns_chat_google_generative_ai(self):
        """google_api_key가 있으면 ChatGoogleGenerativeAI 인스턴스를 반환한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"

            result = get_chat_model(provider="gemini")

            mock_cls.assert_called_once()
            assert result is mock_cls.return_value

    def test_openai_returns_chat_openai(self):
        """openai_api_key가 있으면 ChatOpenAI 인스턴스를 반환한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_cls,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            result = get_chat_model(provider="openai")

            mock_cls.assert_called_once()
            assert result is mock_cls.return_value

    def test_default_provider_from_settings(self):
        """provider 인자 생략 시 settings.llm_provider를 사용한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI"),
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            # provider 인자 없이 호출 — settings.llm_provider="openai"가 적용되어야 한다
            get_chat_model()  # ConfigurationException 없이 통과하면 OK

    # --- timeout/max_retries I/O 레이어 전달 (회귀 안전성 핵심) ---

    def test_gemini_default_timeout_and_retries_unchanged(self):
        """인자 미전달 시 Gemini SDK에 기존 하드코딩값(timeout=30, max_retries=3)을 그대로 전달한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"

            get_chat_model(provider="gemini")

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["timeout"] == 30
            assert kwargs["max_retries"] == 3

    def test_openai_default_timeout_and_retries_unchanged(self):
        """인자 미전달 시 OpenAI SDK에 기존 하드코딩값(request_timeout=30, max_retries=3)을 전달한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_cls,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            get_chat_model(provider="openai")

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["request_timeout"] == 30
            assert kwargs["max_retries"] == 3

    def test_gemini_custom_timeout_and_retries_passthrough(self):
        """전달된 timeout/max_retries가 Gemini SDK 인자로 그대로 내려간다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"

            get_chat_model(provider="gemini", timeout=8, max_retries=1)

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["timeout"] == 8
            assert kwargs["max_retries"] == 1

    def test_openai_custom_timeout_and_retries_passthrough(self):
        """전달된 timeout/max_retries가 OpenAI SDK 인자(request_timeout)로 내려간다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_cls,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.llm_http_max_connections = 400

            get_chat_model(provider="openai", timeout=8, max_retries=1)

            kwargs = mock_cls.call_args.kwargs
            assert kwargs["request_timeout"] == 8
            assert kwargs["max_retries"] == 1


# ---------------------------------------------------------------------------
# get_embeddings 팩토리
# ---------------------------------------------------------------------------


class TestGetEmbeddings:
    """get_embeddings 팩토리 함수 테스트."""

    def test_raises_when_google_api_key_missing(self):
        """google_api_key 없으면 ConfigurationException."""
        with patch("llm.client.settings") as mock_settings:
            mock_settings.google_api_key = None

            with pytest.raises(ConfigurationException, match="GOOGLE_API_KEY"):
                get_embeddings()

    def test_returns_gemini_embeddings_instance(self):
        """google_api_key가 있으면 _GeminiEmbeddings 인스턴스를 반환한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.GoogleGenerativeAIEmbeddings") as mock_cls,
        ):
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.embedding_model = "models/gemini-embedding-2-preview"

            result = get_embeddings()

            mock_cls.assert_called_once()
            assert isinstance(result, _GeminiEmbeddings)
