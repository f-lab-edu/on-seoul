"""middleware/metrics.py — ProcessTimeMiddleware 단위 테스트."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app() -> FastAPI:
    from middleware.metrics import ProcessTimeMiddleware

    app = FastAPI()
    app.add_middleware(ProcessTimeMiddleware)

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


class TestProcessTimeMiddleware:
    async def test_x_process_time_header_present(self):
        """일반 엔드포인트 응답에 X-Process-Time 헤더가 포함된다."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ping")
        assert "x-process-time" in response.headers

    async def test_x_process_time_is_float(self):
        """X-Process-Time 값이 소수점을 포함한 숫자 문자열이다."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ping")
        val = float(response.headers["x-process-time"])
        assert val >= 0.0

    async def test_x_process_time_three_decimal_places(self):
        """X-Process-Time 값이 소수점 3자리로 포맷된다."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/ping")
        val_str = response.headers["x-process-time"]
        # 소수점 이하 자릿수 확인
        assert "." in val_str
        decimal_part = val_str.split(".")[1]
        assert len(decimal_part) == 3

    async def test_health_endpoint_skipped(self):
        """/health 경로는 X-Process-Time 헤더를 추가하지 않는다."""
        app = _make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        assert "x-process-time" not in response.headers

    async def test_docs_endpoint_skipped(self):
        """/docs 경로는 X-Process-Time 헤더를 추가하지 않는다."""
        from middleware.metrics import ProcessTimeMiddleware

        app = FastAPI()
        app.add_middleware(ProcessTimeMiddleware)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/docs")
        assert "x-process-time" not in response.headers

    async def test_openapi_json_endpoint_skipped(self):
        """/openapi.json 경로는 X-Process-Time 헤더를 추가하지 않는다."""
        from middleware.metrics import ProcessTimeMiddleware

        app = FastAPI()
        app.add_middleware(ProcessTimeMiddleware)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/openapi.json")
        assert "x-process-time" not in response.headers
