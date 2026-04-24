import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from core.logging import setup_logging
from routers import chat

setup_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="on-seoul-agent",
    description="서울 공공서비스 예약 AI Agent 서비스",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# 전역 catch-all 미들웨어
# ---------------------------------------------------------------------------


class _CatchAllMiddleware(BaseHTTPMiddleware):
    # NOTE: StreamingResponse의 generator 내부 예외는 이 미들웨어가 잡지 못한다.
    # SSE 오류 처리는 routers/chat.py의 _stream() 내부 except 블록에서 담당한다.
    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        try:
            return await call_next(request)
        except Exception:
            logger.exception("처리되지 않은 예외")
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal server error"},
            )


app.add_middleware(_CatchAllMiddleware)

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
    """Pydantic 검증 오류 → 422 JSON 응답."""
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
