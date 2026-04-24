import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.logging import setup_logging
from routers import chat

setup_logging()

app = FastAPI(
    title="on-seoul-agent",
    description="서울 공공서비스 예약 AI Agent 서비스",
    version="0.1.0",
)

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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """미처리 예외 → 500 JSON 응답."""
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


# ---------------------------------------------------------------------------
# 헬스체크
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
