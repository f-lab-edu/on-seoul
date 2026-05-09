import json
import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.logging import setup_logging
from middleware.metrics import ProcessTimeMiddleware
from routers import chat

setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="on-seoul-agent",
    description="서울 공공서비스 예약 AI Agent 서비스",
    version="0.1.0",
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
            await send({"type": "http.response.start", "status": 500,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})


app.add_middleware(_CatchAllMiddleware)
app.add_middleware(ProcessTimeMiddleware)

# ---------------------------------------------------------------------------
# 라우터 등록
# ---------------------------------------------------------------------------

app.include_router(chat.router, prefix="/chat")

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
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
