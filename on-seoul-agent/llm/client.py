import asyncio
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from aiolimiter import AsyncLimiter
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_openai import ChatOpenAI

from core.config import settings
from core.exceptions import ConfigurationException

logger = logging.getLogger(__name__)

# Gemini Embedding API: RPM 100 / TPM 30K (무료 티어)
#
# [버스트 방지] max_rate=1 로 버킷 크기를 1로 고정한다.
#   AsyncLimiter(max_rate=N, time_period=T) 의 초기 버킷 용량은 N 이다.
#   max_rate=gemini_embed_rpm, time_period=60 으로 설정하면 버킷이 rpm 개 토큰으로
#   가득 찬 채로 시작 → 첫 rpm 개 요청이 거의 동시에 발사된다 (버스트).
#   max_rate=1, time_period=60/rpm 으로 설정하면 버킷 용량이 1이 되어
#   요청 간격이 60/rpm 초로 고정된다 (버스트 없음).
#
# [재시도] 429 수신 시 지수 백오프 후 재시도 — RPM 외 TPM 초과도 429를 유발하므로
#   limiter가 정상이어도 일시적 스파이크 시 재시도가 필요하다.
_EMBED_INTERVAL: float = 60.0 / settings.gemini_embed_rpm  # 요청 최소 간격(초)
_gemini_embed_limiter = AsyncLimiter(max_rate=1, time_period=_EMBED_INTERVAL)

_EMBED_MAX_RETRIES: int = 5
_EMBED_RETRY_BASE_DELAY: float = 10.0  # 첫 429 대기 시간(초), 이후 2배씩 증가


def _rate_limited(limiter: AsyncLimiter) -> Callable:
    """비동기 함수를 AsyncLimiter로 감싸는 데코레이터."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with limiter:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


class _GeminiEmbeddings(Embeddings):
    """GoogleGenerativeAIEmbeddings 래퍼.

    문제 1 — aembed_documents 배치 버그 우회:
        langchain-google-genai 의 aembed_documents 는 내부에서 배치를 단일 호출로
        합치는 버그가 있다. aembed_query 를 개별 호출하여 우회한다.

    문제 2 — aiolimiter 버스트 제거:
        AsyncLimiter(max_rate=N) 는 버킷이 N 개 토큰으로 가득 찬 채로 시작한다.
        max_rate=1 로 버킷 크기를 1로 고정하여 요청 간격을 60/rpm 초로 강제한다.

    문제 3 — 429 지수 백오프:
        RPM 한도 외에 TPM(분당 토큰) 초과도 429 를 유발한다.
        aembed_query 는 429 수신 시 지수 백오프 후 재시도한다.

    limiter 파라미터:
        None 이면 모듈 수준 _gemini_embed_limiter(프로덕션 설정) 을 사용한다.
        테스트에서 빠른 limiter 를 주입하면 실제 대기 없이 검증할 수 있다.
    """

    def __init__(
        self,
        base: GoogleGenerativeAIEmbeddings,
        limiter: AsyncLimiter | None = None,
    ) -> None:
        self._base = base
        self._limiter = limiter if limiter is not None else _gemini_embed_limiter

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._base.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._base.embed_query(text)

    async def _aembed_once(self, text: str) -> list[float]:
        """rate-limited 단일 API 호출. aembed_query 의 retry 진입점."""
        async with self._limiter:
            return await self._base.aembed_query(text)

    async def aembed_query(self, text: str) -> list[float]:
        """rate limit + 429 지수 백오프 재시도."""
        for attempt in range(_EMBED_MAX_RETRIES):
            try:
                return await self._aembed_once(text)
            except Exception as exc:
                is_rate_limit = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if is_rate_limit and attempt < _EMBED_MAX_RETRIES - 1:
                    delay = _EMBED_RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Gemini embed 429 (시도 %d/%d). %ds 후 재시도.",
                        attempt + 1,
                        _EMBED_MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """순차 처리 — 동시 발사 금지."""
        results = []
        for text in texts:
            results.append(await self.aembed_query(text))
        return results


def get_chat_model(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    streaming: bool = False,
) -> BaseChatModel:
    """Return a configured chat LLM instance.

    Gemini를 기본으로 사용하고, provider="openai" 지정 시 GPT로 전환한다.
    """
    selected_provider = provider or settings.llm_provider

    if selected_provider in ("gemini", "google"):
        if not settings.google_api_key:
            raise ConfigurationException("GOOGLE_API_KEY is required for Gemini provider")
        return ChatGoogleGenerativeAI(
            google_api_key=settings.google_api_key,
            model=model or settings.gemini_model,
            temperature=temperature,
        )
    elif selected_provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationException("OPENAI_API_KEY is required for OpenAI provider")
        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=model or settings.gpt_model,
            temperature=temperature,
            streaming=streaming,
        )
    else:
        raise ConfigurationException(
            f"Unknown LLM provider: {selected_provider!r}. Use 'gemini' or 'openai'."
        )


def get_embeddings(model: str | None = None) -> Embeddings:
    """Return a configured embeddings instance.

    Gemini gemini-embedding-2-preview, output_dimensionality=1536 (DDL vector(1536) 기준).
    """
    if not settings.google_api_key:
        raise ConfigurationException("GOOGLE_API_KEY is required for Gemini embeddings")
    base = GoogleGenerativeAIEmbeddings(
        google_api_key=settings.google_api_key,
        model=model or settings.embedding_model,
        output_dimensionality=1536,
    )
    return _GeminiEmbeddings(base)  # 프로덕션: 모듈 수준 _gemini_embed_limiter 사용
