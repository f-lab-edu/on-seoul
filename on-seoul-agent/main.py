import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from agents.graph import AgentGraph
from core.concurrency import init_global_sema
from core.config import settings
from core.langfuse_client import init_langfuse, shutdown_langfuse
from core.logging import setup_logging
from core.redis import get_redis
from core.telemetry import setup_telemetry, shutdown_telemetry
from llm.client import close_openai_http_client, init_openai_http_client
from middleware.metrics import ProcessTimeMiddleware
from routers import admin as admin_router
from routers import chat
from routers import embeddings as embeddings_router
from routers import notification as notification_router

setup_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """애플리케이션 lifespan — Redis 클라이언트를 process-singleton으로 보관.

    Answer Cache(core/cache.py)가 Redis를 사용하므로 app.state.redis에 보관한다.
    AgentGraph는 이 redis를 주입받아 process 내에서 1회만 컴파일된다.

    vector_global_sema를 이벤트 루프 시작 이후 초기화하여 Python 3.10+ 경고를 방지한다.
    """
    # Langfuse LLM 계측 — langfuse_enabled=False(기본)이거나 키 미설정 시 no-op.
    # OTel(인프라 계측)과 별개 파이프라인으로 공존하며, 그래프 config 의 callbacks 로
    # LLM I/O·토큰·비용을 관측한다 (core/langfuse_client.py).
    init_langfuse()

    # 글로벌 VECTOR fan-out 세마포어 초기화 — 이벤트 루프 생성 후 실행.
    # core/concurrency.py 모듈 전역 변수에 등록하여 VectorAgent가 직접 참조한다.
    init_global_sema()
    logger.info(
        "vector_global_sema 초기화: concurrency=%d", settings.vector_global_concurrency
    )

    # OpenAI provider용 httpx.AsyncClient 싱글톤 초기화.
    # 요청마다 새 AsyncClient를 생성하면 FD 누수가 발생하므로 lifespan에서 1회 초기화한다.
    init_openai_http_client()
    logger.info(
        "openai_http_client 초기화: max_connections=%d", settings.llm_http_max_connections
    )

    redis = get_redis()
    app.state.redis = redis
    app.state.graph = AgentGraph(redis=redis)
    try:
        yield
    finally:
        try:
            await redis.aclose()
        except Exception:
            logger.warning("redis aclose 실패", exc_info=True)
        try:
            await close_openai_http_client()
        except Exception:
            logger.warning("openai_http_client aclose 실패", exc_info=True)
        shutdown_telemetry()
        shutdown_langfuse()


app = FastAPI(
    title="on-seoul-agent",
    description="서울 공공서비스 예약 AI Agent 서비스",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# 전역 catch-all 미들웨어 (순수 ASGI)
# ---------------------------------------------------------------------------


class _CatchAllMiddleware:
    """처리되지 않은 예외를 500으로 변환하는 순수 ASGI 미들웨어.

    BaseHTTPMiddleware 대신 순수 ASGI 미들웨어로 구현하여
    SSE StreamingResponse와의 CancelScope 충돌을 방지한다.
    NOTE: StreamingResponse generator 내부 예외는 여기서 잡히지 않는다.
          SSE 오류 처리는 routers/chat.py의 _stream() except 블록에서 담당한다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        except Exception:
            logger.exception("처리되지 않은 예외")
            body = json.dumps({"detail": "Internal server error"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})


app.add_middleware(_CatchAllMiddleware)
app.add_middleware(ProcessTimeMiddleware)

# OTel 인프라 계측 — otel_enabled=False(기본)이거나 endpoint 미설정 시 no-op.
# 기존 커스텀 트레이싱(chat_agent_traces)과 병행한다.
#
# lifespan 이 아니라 모듈 레벨(서빙 시작 전)에서 호출한다.
# 핵심 제약: FastAPIInstrumentor.instrument_app 은 Starlette 가 ASGI 미들웨어 스택을
# 빌드·캐시하기 *전*에 실행돼야 한다. instrument_app 은 add_middleware 가 아니라
# build_middleware_stack 을 패치해 ServerErrorMiddleware 바로 안쪽에 OTel 미들웨어를
# 주입하므로(=add_middleware 호출 순서와 무관하게 두 커스텀 미들웨어보다 바깥),
# 위치만 맞으면 서버 span 이 전체 요청을 감싸고 traceparent 를 가장 먼저 추출한다.
# lifespan 본문에서 호출하면 스택이 이미 빌드된 뒤라 패치가 반영되지 않아 서버 span
# 자체가 생성되지 않는다(첫 호출=lifespan 스코프에서 스택이 빌드되기 때문).
setup_telemetry(app)

# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------

app.include_router(chat.router, prefix="/chat")
app.include_router(admin_router.router)
app.include_router(embeddings_router.router)
app.include_router(notification_router.router)

# ---------------------------------------------------------------------------
# 전역 에러 핸들러
# ---------------------------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic 검증 오류 → 422 JSON 응답 + 요청 본문 로그."""
    try:
        body = await request.body()
        body_text = body.decode("utf-8") if body else "(empty)"
    except Exception:
        body_text = "(읽기 실패)"

    logger.warning(
        "422 Unprocessable Content | %s %s | body: %s | errors: %s",
        request.method,
        request.url.path,
        body_text,
        exc.errors(),
    )
    # Pydantic v2 errors의 ctx["error"]는 ValueError 등 예외 인스턴스를 담을 수 있다.
    # Python 표준 json.dumps는 예외 인스턴스를 직렬화하지 못하므로 문자열로 변환한다.
    errors = exc.errors()
    for err in errors:
        ctx = err.get("ctx")
        if ctx and "error" in ctx and isinstance(ctx["error"], Exception):
            ctx["error"] = str(ctx["error"])
        # input 필드가 너무 크면 직렬화 오류 가능성이 있으므로 제거한다.
        err.pop("url", None)
    return JSONResponse(
        status_code=422,
        content={"detail": errors},
    )


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
