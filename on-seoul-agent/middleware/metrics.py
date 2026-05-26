"""요청별 처리 시간을 X-Process-Time 응답 헤더에 추가하는 미들웨어.

BaseHTTPMiddleware 대신 순수 ASGI 미들웨어로 구현한다.
BaseHTTPMiddleware는 내부적으로 anyio CancelScope를 사용하여
StreamingResponse(SSE)와 함께 쓰면 응답 스트리밍 중 asyncpg 연결이
강제 종료되는 문제가 있다.

순수 ASGI 미들웨어는 send 콜백을 래핑하는 방식이므로
SSE 스트림을 전혀 간섭하지 않는다.

skip 경로(/health, /docs, /openapi.json)는 헤더를 추가하지 않는다.
"""

import time

from starlette.types import ASGIApp, Receive, Scope, Send

_SKIP_PATHS = frozenset({"/health", "/docs", "/openapi.json"})


class ProcessTimeMiddleware:
    """X-Process-Time 헤더를 응답에 추가하는 순수 ASGI 미들웨어.

    http.response.start 메시지를 가로채 헤더를 주입한다.
    SSE StreamingResponse와 호환된다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        start = time.monotonic()

        async def send_with_process_time(message: dict) -> None:
            if message["type"] == "http.response.start":
                elapsed = time.monotonic() - start
                # headers는 list[tuple[bytes, bytes]]
                headers = list(message.get("headers", []))
                headers.append((b"x-process-time", f"{elapsed:.3f}".encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_process_time)
