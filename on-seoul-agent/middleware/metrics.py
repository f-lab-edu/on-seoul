"""요청별 처리 시간을 X-Process-Time 응답 헤더에 추가하는 미들웨어.

skip 경로(/health, /docs, /openapi.json)는 헤더를 추가하지 않는다.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_SKIP_PATHS = frozenset({"/health", "/docs", "/openapi.json"})


class ProcessTimeMiddleware(BaseHTTPMiddleware):
    """X-Process-Time 헤더를 응답에 추가한다.

    값은 요청 처리 시간(초)을 소수점 3자리로 포맷한 문자열이다.
    skip 경로에서는 헤더를 추가하지 않아 불필요한 측정 오버헤드를 방지한다.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        start = time.monotonic()
        response: Response = await call_next(request)
        elapsed = time.monotonic() - start
        response.headers["X-Process-Time"] = f"{elapsed:.3f}"
        return response
