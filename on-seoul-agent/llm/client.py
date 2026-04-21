import asyncio
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

# Gemini Embedding API: RPM 100 / TPM 30K (무료 티어)
# 기본값은 유료 기준 60 rpm으로 보수적으로 설정. settings로 재정의 가능.
_gemini_embed_limiter = AsyncLimiter(
    max_rate=settings.gemini_embed_rpm,
    time_period=60,
)


def _rate_limited(limiter: AsyncLimiter) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with limiter:
                return await func(*args, **kwargs)

        return wrapper

    return decorator


class _GeminiEmbeddings(Embeddings):
    """GoogleGenerativeAIEmbeddings 래퍼.

    aembed_documents가 배치를 단일 호출로 합치는 버그를 우회한다.
    aembed_query를 asyncio.gather로 병렬 호출하여 각 텍스트의 벡터를 독립적으로 얻는다.
    rate limiter를 적용하여 대량 배치 처리 시 API 한도 초과를 방지한다.
    """

    def __init__(self, base: GoogleGenerativeAIEmbeddings) -> None:
        self._base = base

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._base.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._base.embed_query(text)

    @_rate_limited(_gemini_embed_limiter)
    async def aembed_query(self, text: str) -> list[float]:
        return await self._base.aembed_query(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return list(await asyncio.gather(*[self.aembed_query(t) for t in texts]))


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
        return ChatGoogleGenerativeAI(
            google_api_key=settings.google_api_key,
            model=model or settings.gemini_model,
            temperature=temperature,
        )
    elif selected_provider == "openai":
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
    base = GoogleGenerativeAIEmbeddings(
        google_api_key=settings.google_api_key,
        model=model or settings.embedding_model,
        output_dimensionality=1536,
    )
    return _GeminiEmbeddings(base)
