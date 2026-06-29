import asyncio
import logging
import random
from collections.abc import AsyncIterator, Callable, Iterator
from functools import wraps
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from google.api_core.exceptions import (
    DeadlineExceeded,
    InternalServerError,
    ResourceExhausted,
    ServiceUnavailable,
)
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from core.config import settings
from core.exceptions import ConfigurationException, RateLimitException


# ---------------------------------------------------------------------------
# 모델 티어 fallback — 일시적 오류 집합
# ---------------------------------------------------------------------------
# primary 모델 호출이 *일시적 오류*로 실패할 때만 fallback 모델로 재시도한다.
# ConfigurationException·ValueError·ValidationError 같은 명백한 버그/설정 오류는
# 포함하지 않는다(헛된 재호출 방지). LangChain with_fallbacks(exceptions_to_handle=)
# 와 _FallbackChatModel._agenerate/_generate 위임 판정에 동일하게 쓰인다.
def _build_transient_exc() -> tuple[type[BaseException], ...]:
    excs: list[type[BaseException]] = [
        # google.api_core: 429 / 503 / 500 / timeout
        ResourceExhausted,
        ServiceUnavailable,
        InternalServerError,
        DeadlineExceeded,
        # httpx 네트워크/타임아웃
        httpx.TimeoutException,
        httpx.TransportError,
        # 임베딩/LLM rate limit 소진(이미 정의됨)
        RateLimitException,
        # 구조화 출력 파싱 실패 → fallback 모델로 재시도
        OutputParserException,
    ]
    # primary가 openai일 때를 위한 조건부 포함(import 가용 시).
    try:
        import openai

        excs.extend(
            [
                openai.RateLimitError,
                openai.APITimeoutError,
                openai.InternalServerError,
                openai.APIConnectionError,
            ]
        )
    except ImportError:  # pragma: no cover
        pass
    return tuple(excs)


_TRANSIENT_EXC: tuple[type[BaseException], ...] = _build_transient_exc()

# ---------------------------------------------------------------------------
# 프로세스 전역 httpx.AsyncClient (OpenAI provider 전용)
# ---------------------------------------------------------------------------
# get_chat_model(provider="openai")가 호출될 때마다 새 AsyncClient를 생성하면
# FD 누수가 발생한다. 특히 routers/notification.py·routers/embeddings.py는
# 요청마다 get_chat_model()을 호출하므로 반드시 싱글톤으로 공유해야 한다.
#
# 초기화: main.py lifespan에서 init_openai_http_client()를 호출한다.
# 종료:   main.py lifespan 종료 시 close_openai_http_client()를 호출한다.
# None:   lifespan 이전(테스트·임포트 시점). 이 경우 get_chat_model()이
#         임시 AsyncClient를 생성한다(테스트 환경에서 ChatOpenAI를 mock하므로 무해).
_openai_http_client: httpx.AsyncClient | None = None


def init_openai_http_client() -> httpx.AsyncClient:
    """lifespan 시작 시 호출하여 OpenAI용 httpx.AsyncClient 싱글톤을 초기화한다."""
    global _openai_http_client  # noqa: PLW0603
    _openai_http_client = httpx.AsyncClient(
        limits=_make_httpx_limits(settings.llm_http_max_connections),
    )
    return _openai_http_client


async def close_openai_http_client() -> None:
    """lifespan 종료 시 호출하여 OpenAI용 httpx.AsyncClient를 닫는다."""
    global _openai_http_client  # noqa: PLW0603
    if _openai_http_client is not None:
        await _openai_http_client.aclose()
        _openai_http_client = None

logger = logging.getLogger(__name__)


def _make_httpx_limits(max_connections: int) -> httpx.Limits:
    """httpx.Limits 인스턴스를 반환한다.

    keepalive_expiry=30: 유휴 keep-alive 연결이 30초 후 회수되어 FD 누수를 방지한다.
    max_keepalive_connections=100: 풀에 유지할 최대 keep-alive 소켓 수.
    """
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=100,
        keepalive_expiry=30,
    )


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
_EMBED_RETRY_MAX_DELAY: float = 60.0  # 단일 재시도 최대 대기 시간(초)


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
        # rate limiting 없이 실행됨 — 가능하면 aembed_documents를 사용할 것
        logger.warning(
            "embed_documents: sync 경로는 rate limiting이 적용되지 않습니다. aembed_documents 사용을 권장합니다."
        )
        return self._base.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        # rate limiting 없이 실행됨 — 가능하면 aembed_query를 사용할 것
        logger.warning(
            "embed_query: sync 경로는 rate limiting이 적용되지 않습니다. aembed_query 사용을 권장합니다."
        )
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
                is_rate_limit = isinstance(exc, ResourceExhausted)
                if is_rate_limit and attempt < _EMBED_MAX_RETRIES - 1:
                    delay = random.uniform(
                        0,
                        min(_EMBED_RETRY_BASE_DELAY * (2**attempt), _EMBED_RETRY_MAX_DELAY),
                    )
                    logger.warning(
                        "Gemini embed 429 (시도 %d/%d). %.1fs 후 재시도.",
                        attempt + 1,
                        _EMBED_MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
                elif is_rate_limit:
                    raise RateLimitException(
                        f"Gemini embed rate limit 소진 (최대 {_EMBED_MAX_RETRIES}회 재시도)",
                        detail=exc,
                    ) from exc
                else:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """순차 처리 — 동시 발사 금지."""
        results = []
        for text in texts:
            results.append(await self.aembed_query(text))
        return results


class _FallbackChatModel(BaseChatModel):
    """primary + fallback 모델을 품은 얇은 BaseChatModel 래퍼.

    "모델 티어 fallback": primary 모델 호출이 *일시적 오류*(_TRANSIENT_EXC)로
    실패하면 fallback 모델로 자동 재시도한다. 벤더 fallback과는 별개이며 fallback은
    항상 Gemini provider로 빌드된다(get_chat_model 참조).

    두 가지 소비 경로를 모두 투명하게 지원한다:
      (a) `.with_structured_output(Schema)` 경로 — with_structured_output을 각
          모델에 먼저 적용한 뒤 RunnableWithFallbacks로 묶는다. RunnableWithFallbacks
          자체에는 with_structured_output이 없으므로 합성 순서가 핵심이다.
      (b) `prompt | llm | StrOutputParser()` raw 파이프 경로 — ainvoke가 결국
          _agenerate를 호출하므로, _agenerate에서 primary 실패 시 fallback에 위임한다.

    관측 한계: fallback 위임 시 primary가 시작한 run_manager를 그대로 재사용한다.
    BaseChatModel이 on_llm_start를 primary 기준으로 이미 발행한 뒤이므로, fallback
    응답은 콜백 트레이스(Langfuse/OTel)상 primary run에 귀속된다 — 실제로 어느 모델이
    응답했는지는 위임 시 남기는 logger.warning으로만 구분된다.
    """

    _primary: BaseChatModel = PrivateAttr()
    _fallback: BaseChatModel = PrivateAttr()
    _exc: tuple[type[BaseException], ...] = PrivateAttr()

    def __init__(
        self,
        primary: BaseChatModel,
        fallback: BaseChatModel,
        exceptions_to_handle: tuple[type[BaseException], ...] = _TRANSIENT_EXC,
    ) -> None:
        super().__init__()
        self._primary = primary
        self._fallback = fallback
        self._exc = exceptions_to_handle

    @property
    def _llm_type(self) -> str:
        return "fallback_chat_model"

    # --- (a) 구조화 출력 경로 ---------------------------------------------
    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:
        # 각 모델에 with_structured_output을 먼저 적용한 뒤 fallback으로 묶는다.
        # 순서를 뒤집으면(RunnableWithFallbacks.with_structured_output) AttributeError.
        return self._primary.with_structured_output(schema, **kwargs).with_fallbacks(
            [self._fallback.with_structured_output(schema, **kwargs)],
            exceptions_to_handle=self._exc,
        )

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:
        # with_structured_output과 동일 패턴. 현재 사용처는 없으나 대칭성을 위해 제공.
        return self._primary.bind_tools(tools, **kwargs).with_fallbacks(
            [self._fallback.bind_tools(tools, **kwargs)],
            exceptions_to_handle=self._exc,
        )

    # --- (b) raw 파이프 경로 (ainvoke → _agenerate) -----------------------
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return await self._primary._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        except self._exc as exc:
            logger.warning(
                "primary 모델 일시적 오류(%s) → fallback 모델로 재시도",
                type(exc).__name__,
            )
            return await self._fallback._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            return self._primary._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        except self._exc as exc:
            logger.warning(
                "primary 모델 일시적 오류(%s) → fallback 모델로 재시도",
                type(exc).__name__,
            )
            return self._fallback._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )

    # --- streaming: best-effort -------------------------------------------
    # 현재 어떤 호출부도 streaming=True 모델을 만들지 않으므로 hot path가 아니다.
    # 첫 청크 이전(스트림 시작 전) primary 실패 시에만 fallback으로 위임한다.
    # mid-stream 복구(일부 청크 yield 후 실패)는 지원하지 않는다.
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        try:
            stream = self._primary._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
            first = await stream.__anext__()
        except StopAsyncIteration:
            return
        except self._exc as exc:
            logger.warning(
                "primary 스트림 시작 실패(%s) → fallback 스트림으로 재시도",
                type(exc).__name__,
            )
            async for chunk in self._fallback._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                yield chunk
            return
        yield first
        async for chunk in stream:
            yield chunk

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        try:
            stream = self._primary._stream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
            first = next(stream)
        except StopIteration:
            return
        except self._exc as exc:
            logger.warning(
                "primary 스트림 시작 실패(%s) → fallback 스트림으로 재시도",
                type(exc).__name__,
            )
            yield from self._fallback._stream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
            return
        yield first
        yield from stream


def _build_chat_model(
    selected_provider: str,
    model: str | None,
    temperature: float,
    streaming: bool,
    timeout: int,
    max_retries: int,
) -> BaseChatModel:
    """raw chat 모델 빌더 — fallback 래핑 없이 단일 SDK 인스턴스를 만든다.

    get_chat_model이 primary와 (Gemini) fallback을 둘 다 이 헬퍼로 만든 뒤
    _FallbackChatModel로 한 번만 감싼다. fallback을 get_chat_model 재귀로 만들면
    무한 래핑이 되므로 반드시 이 헬퍼를 거친다.
    """
    if selected_provider in ("gemini", "google"):
        if not settings.google_api_key:
            raise ConfigurationException(
                "GOOGLE_API_KEY is required for Gemini provider"
            )
        # ChatGoogleGenerativeAI는 google-generativeai SDK를 사용하며
        # httpx.AsyncClient를 직접 주입하는 공식 파라미터가 없다.
        # google-auth 내부 transport는 requests(동기)이므로 httpx 풀 설정 대상 외.
        return ChatGoogleGenerativeAI(
            google_api_key=settings.google_api_key,
            model=model or settings.gemini_model,
            temperature=temperature,
            max_retries=max_retries,
            timeout=timeout,
        )
    elif selected_provider == "openai":
        if not settings.openai_api_key:
            raise ConfigurationException(
                "OPENAI_API_KEY is required for OpenAI provider"
            )
        # ChatOpenAI → openai.AsyncOpenAI → httpx.AsyncClient.
        # 모듈 전역 _openai_http_client(lifespan 싱글톤)을 재사용하여 FD 누수를 방지한다.
        # lifespan 이전(테스트 환경)에는 None이므로 임시 클라이언트를 생성한다.
        # 테스트는 ChatOpenAI를 mock하므로 임시 클라이언트가 실제로 사용되지 않아 무해하다.
        http_client = _openai_http_client or httpx.AsyncClient(
            limits=_make_httpx_limits(settings.llm_http_max_connections),
        )
        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=model or settings.gpt_model,
            temperature=temperature,
            streaming=streaming,
            max_retries=max_retries,
            request_timeout=timeout,
            async_client=http_client,
        )
    else:
        raise ConfigurationException(
            f"Unknown LLM provider: {selected_provider!r}. Use 'gemini' or 'openai'."
        )


def get_chat_model(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    streaming: bool = False,
    timeout: int = 30,
    max_retries: int = 3,
) -> BaseChatModel:
    """Return a configured chat LLM instance.

    Gemini를 기본으로 사용하고, provider="openai" 지정 시 GPT로 전환한다.

    timeout/max_retries는 SDK(I/O 레이어)로 직접 내려보내 in-flight HTTP 요청을
    실제 소켓 레벨에서 끊게 한다. 기본값은 기존 하드코딩값과 동일하다.

    모델 티어 fallback(settings.llm_fallback_enabled=True, 기본):
      primary 모델이 일시적 오류로 실패하면 settings.gemini_fallback_model
      (기본 gemini-3.1-flash-lite)로 자동 재시도하는 _FallbackChatModel로 감싼다.
      호출부 코드 변경은 0이다(with_structured_output / raw 파이프 둘 다 투명 지원).

      비활성(False)이거나 fallback(Gemini)에 필요한 google_api_key가 없으면
      raw primary 모델을 그대로 반환한다(하위호환·크래시 금지).
    """
    selected_provider = provider or settings.llm_provider

    primary = _build_chat_model(
        selected_provider, model, temperature, streaming, timeout, max_retries
    )

    if not settings.llm_fallback_enabled:
        return primary

    # fallback은 항상 Gemini provider. 키가 없으면 fallback 없이 primary만 반환.
    if not settings.google_api_key:
        logger.warning(
            "llm_fallback_enabled=True 이나 GOOGLE_API_KEY 부재 → "
            "모델 티어 fallback 없이 primary 모델만 사용한다."
        )
        return primary

    fallback = _build_chat_model(
        "gemini",
        settings.gemini_fallback_model,
        temperature,
        streaming,
        timeout,
        max_retries,
    )
    return _FallbackChatModel(primary=primary, fallback=fallback)


def get_embeddings(model: str | None = None) -> Embeddings:
    """Return a configured embeddings instance.

    Gemini gemini-embedding-2-preview, output_dimensionality=768 (DDL vector(768) 기준).

    GoogleGenerativeAIEmbeddings는 내부적으로 google-generativeai SDK를 사용한다.
    langchain-google-genai 0.x에서는 transport로 requests(동기) 또는 aiohttp(비동기)를
    사용한다. httpx.AsyncClient 직접 주입 파라미터가 없으므로 httpx 풀 설정은
    LLM(OpenAI provider) 경로에만 적용된다. Gemini 임베딩의 동시성은
    _gemini_embed_limiter(RPM) + asyncio.Semaphore(vector_global_concurrency)로 제어한다.
    """
    if not settings.google_api_key:
        raise ConfigurationException("GOOGLE_API_KEY is required for Gemini embeddings")
    base = GoogleGenerativeAIEmbeddings(
        google_api_key=settings.google_api_key,
        model=model or settings.embedding_model,
        output_dimensionality=768,
    )
    return _GeminiEmbeddings(base)  # 프로덕션: 모듈 수준 _gemini_embed_limiter 사용
