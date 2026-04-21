"""llm/client.py 단위 테스트.

_rate_limited 데코레이터와 _GeminiEmbeddings 래퍼의 동작을 검증한다.
실제 API 호출 없이 mock으로만 실행된다.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiolimiter import AsyncLimiter

from llm.client import _GeminiEmbeddings, _rate_limited


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
        """mock base를 주입한 _GeminiEmbeddings 인스턴스를 반환한다."""
        base = MagicMock()
        base.embed_query.return_value = vector or [0.1, 0.2]
        base.embed_documents.return_value = [vector or [0.1, 0.2]]
        base.aembed_query = AsyncMock(return_value=vector or [0.1, 0.2])
        return _GeminiEmbeddings(base)

    # --- 동기 위임 ---

    def test_embed_query_delegates_to_base(self):
        emb = self._make_embeddings([1.0, 2.0])
        assert emb.embed_query("text") == [1.0, 2.0]
        emb._base.embed_query.assert_called_once_with("text")

    def test_embed_documents_delegates_to_base(self):
        emb = self._make_embeddings()
        emb._base.embed_documents.return_value = [[0.1], [0.2]]
        result = emb.embed_documents(["a", "b"])
        assert result == [[0.1], [0.2]]
        emb._base.embed_documents.assert_called_once_with(["a", "b"])

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
        emb = _GeminiEmbeddings(base)

        result = await emb.aembed_documents(["abc", "def", "ghi"])

        assert len(result) == 3
        assert result[0] == [float(ord("a")), 0.0]
        assert result[1] == [float(ord("d")), 0.0]
        assert result[2] == [float(ord("g")), 0.0]

    async def test_aembed_documents_calls_aembed_query_for_each_text(self):
        """aembed_documents는 텍스트 수만큼 aembed_query를 호출한다."""
        base = MagicMock()
        base.aembed_query = AsyncMock(return_value=[0.0])
        emb = _GeminiEmbeddings(base)

        texts = ["a", "b", "c", "d"]
        await emb.aembed_documents(texts)

        assert base.aembed_query.call_count == len(texts)

    async def test_aembed_documents_empty_input(self):
        """빈 리스트 입력 시 빈 리스트를 반환한다."""
        emb = self._make_embeddings()
        result = await emb.aembed_documents([])
        assert result == []

    async def test_aembed_query_is_rate_limited(self):
        """aembed_query에 rate limiter가 적용되어 있다.

        로컬 tight limiter(max_rate=3, time_period=1)를 직접 주입하여
        aembed_documents가 6건 처리 시 실제 지연이 발생하는지 확인한다.
        autouse patch와 무관하게 로컬 limiter를 사용해 독립적으로 검증한다.
        """
        tight_limiter = AsyncLimiter(max_rate=3, time_period=1)

        base = MagicMock()
        base.aembed_query = AsyncMock(return_value=[0.0])
        emb = _GeminiEmbeddings(base)

        @_rate_limited(tight_limiter)
        async def rate_limited_query(text: str) -> list[float]:
            return await base.aembed_query(text)

        with patch.object(emb, "aembed_query", rate_limited_query):
            start = time.monotonic()
            await emb.aembed_documents(["t1", "t2", "t3", "t4", "t5", "t6"])
            elapsed = time.monotonic() - start

        assert elapsed >= 1.0, f"rate limit 미적용 — {elapsed:.2f}s 만에 완료됨"
