"""FastAPI 서버 span 실제 생성 회귀 테스트 (OTel instrument_app 시점 수정).

이 수정의 *목적* 은 "FastAPI 서버 span 이 실제로 emit 된다" 이다. 근본 원인은
instrument_app 을 lifespan 본문에서 호출하면 Starlette 가 이미 ASGI 미들웨어
스택을 빌드·캐시한 뒤라서 OTel ASGI 미들웨어가 반영되지 않아 서버 span 이 만들어
지지 않는 것이었다. 수정은 instrument_app(=setup_telemetry) 호출을 모듈 레벨
(미들웨어 등록 직후, 서빙/스택 빌드 전)로 옮겼다.

검증 전략 (InMemorySpanExporter + 실제 ASGI 호출):
- (A) 수정 후 순서(instrument → 스택 빌드): POST 서버 span 이 생성된다.
- (B) 수정 전 순서(스택 빌드/캐시 → instrument): 서버 span 이 생성되지 않는다.
  => (B) 가 곧 sabotage 증거다. 두 케이스를 한 테스트에서 대조하므로, 수정이
     되돌아가(=instrument 가 캐시 후 호출) 동작이 (B) 로 바뀌면 (A) 단언이 깨진다.
- traceparent 헤더가 있는 요청에서 서버 span 이 그 trace_id 를 상속(같은 trace,
  들어온 span 을 부모로)하는지.
- OTel 미들웨어가 _CatchAllMiddleware / ProcessTimeMiddleware 보다 최외곽인지
  (traceparent 최우선 추출 + 500 변환·ProcessTime 동작 보존).

실제 exporter 전송 없음(InMemorySpanExporter). LLM/DB 호출 없음.
"""

import asyncio
import logging

import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from middleware.metrics import ProcessTimeMiddleware


# ---------------------------------------------------------------------------
# 공통 헬퍼 — 전역 TracerProvider 를 SDK provider 로 교체(테스트 후 원복).
# FastAPIInstrumentor 는 tracer_provider 미지정 시 전역 provider 를 사용한다.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_root_logger_handlers():
    """루트 로거 핸들러 + telemetry 전역 상태를 테스트 후 원복한다.

    OTel 활성 경로는 _setup_logs 에서 LoggingHandler(테스트에선 mock)를 루트
    로거에 부착한다. mock 핸들러의 .level 은 MagicMock 이라 다른 테스트의 로깅을
    깨뜨리므로(다른 모듈로 누수 방지) 핸들러 스냅샷을 원복한다.
    """
    saved = list(logging.getLogger().handlers)
    yield
    logging.getLogger().handlers = saved


@pytest.fixture()
def sdk_provider():
    """전역 TracerProvider 를 SDK provider 로 일시 교체하고 exporter 를 노출한다."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # trace.set_tracer_provider 는 1회만 허용(override 경고)하므로 내부 변수 교체.
    saved = trace._TRACER_PROVIDER
    trace._TRACER_PROVIDER = provider
    try:
        yield exporter
    finally:
        trace._TRACER_PROVIDER = saved
        exporter.clear()


def _make_app() -> FastAPI:
    """main.py 미들웨어 구조를 모사한 최소 앱.

    실제 _CatchAllMiddleware 대신 동등한 500 변환 ASGI 미들웨어를 둔다 — 미들웨어
    순서/서버 span 생성 검증이 목적이므로 라우트는 단순화한다.
    """
    from main import _CatchAllMiddleware  # 실제 catch-all 미들웨어 재사용

    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/boom")
    def _boom() -> dict[str, str]:
        raise RuntimeError("kaboom")  # _CatchAllMiddleware 가 500 으로 변환

    # main.py 와 동일 순서로 등록.
    app.add_middleware(_CatchAllMiddleware)
    app.add_middleware(ProcessTimeMiddleware)
    return app


async def _asgi_get(app: FastAPI, path: str, headers: list | None = None) -> dict:
    """캐시된 middleware_stack 으로 GET 요청을 ASGI 직접 호출한다.

    Starlette 의 서빙 경로를 모사: app.middleware_stack 를 1회 빌드·캐시한 뒤
    그 캐시본으로 요청을 보낸다(=실제 서버의 요청 처리 경로).
    """
    if app.middleware_stack is None:
        app.middleware_stack = app.build_middleware_stack()
    stack = app.middleware_stack

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "headers": headers or [],
        "query_string": b"",
        "client": ("test", 1),
        "server": ("test", 80),
        "scheme": "http",
        "http_version": "1.1",
        "root_path": "",
        "app": app,
    }
    received = [{"type": "http.request", "body": b"", "more_body": False}]
    sent: list[dict] = []

    async def receive():
        return received.pop(0)

    async def send(message):
        sent.append(message)

    await stack(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    return {"status": start["status"], "headers": dict(start["headers"]), "messages": sent}


# ---------------------------------------------------------------------------
# 핵심 — 서버 span 실제 생성 + sabotage 대조.
# ---------------------------------------------------------------------------


def test_server_span_created_only_when_instrumented_before_stack_build(sdk_provider):
    """instrument 가 스택 빌드 *전* 에 호출돼야 GET 서버 span 이 생성된다(sabotage 대조).

    - CASE A (수정 후): instrument → (이후) 스택 빌드/요청 ⇒ 서버 span 생성.
    - CASE B (수정 전/lifespan 타이밍): 스택 빌드·캐시 → instrument → 요청
      ⇒ OTel ASGI 미들웨어가 캐시된 스택에 없어 서버 span 미생성.

    수정을 되돌려 instrument 가 캐시 후(lifespan) 호출되면 동작이 B 로 바뀌어
    CASE A 단언이 즉시 깨진다 — 이 대조가 곧 회귀 가드다.
    """
    exporter = sdk_provider

    # CASE A — 수정 후 순서.
    app_a = _make_app()
    FastAPIInstrumentor.instrument_app(app_a)  # 스택 빌드 전 부착(모듈 레벨 모사)
    try:
        asyncio.run(_asgi_get(app_a, "/ping"))
        names_a = [s.name for s in exporter.get_finished_spans()]
    finally:
        FastAPIInstrumentor.uninstrument_app(app_a)
    exporter.clear()

    # CASE B — 수정 전(lifespan) 순서: 스택을 먼저 빌드·캐시한 뒤 instrument.
    app_b = _make_app()
    app_b.middleware_stack = app_b.build_middleware_stack()  # == lifespan 시점엔 이미 캐시됨
    FastAPIInstrumentor.instrument_app(app_b)  # 너무 늦음
    try:
        asyncio.run(_asgi_get(app_b, "/ping"))
        names_b = [s.name for s in exporter.get_finished_spans()]
    finally:
        FastAPIInstrumentor.uninstrument_app(app_b)

    # 수정 후: 서버 span 생성. (FastAPIInstrumentor 의 서버 span 이름은 "GET /ping")
    assert "GET /ping" in names_a, (
        f"수정 후 순서에서 서버 span 이 없다 — 버그 미수정. spans={names_a}"
    )
    # 수정 전: 서버 span 미생성 — 이 단언이 sabotage 의 'before' 상태를 고정한다.
    assert "GET /ping" not in names_b, (
        f"lifespan 타이밍에서도 서버 span 이 생겼다 — 대조 전제가 깨짐. spans={names_b}"
    )


def test_real_main_app_emits_post_server_span_when_instrumented(sdk_provider):
    """실제 main.app 을 OTel 부착하면 POST 서버 span 이 emit 된다.

    main.app 은 import 시 setup_telemetry 를 호출하지만 otel_enabled=False(기본)
    라서 no-op 이다. 여기서는 동일 app 객체에 직접 instrument_app 을 걸어, main 의
    실제 미들웨어 스택(_CatchAllMiddleware/ProcessTimeMiddleware) 위에서 서버 span
    이 생성되고 스택을 깨지 않음을 확인한다. (라우트는 /health — DB/LLM 무관.)
    """
    exporter = sdk_provider
    from main import app as main_app

    # 다른 테스트가 main.app 스택을 빌드/계측했을 수 있으므로(전체 스위트 순서 무관),
    # 깨끗한 상태로 리셋한 뒤 스택 빌드 전(=올바른 시점) instrument 한다.
    if getattr(main_app, "_is_instrumented_by_opentelemetry", False):
        FastAPIInstrumentor.uninstrument_app(main_app)
    main_app.middleware_stack = None

    FastAPIInstrumentor.instrument_app(main_app)
    try:
        res = asyncio.run(_asgi_get(main_app, "/health"))
        names = [s.name for s in exporter.get_finished_spans()]
    finally:
        FastAPIInstrumentor.uninstrument_app(main_app)
        # instrument_app 이 middleware_stack 을 무효화하므로 다음 사용자 위해 원복.
        main_app.middleware_stack = None

    assert res["status"] == 200
    assert "GET /health" in names, f"main.app 서버 span 미생성. spans={names}"


def test_server_span_inherits_incoming_traceparent(sdk_provider):
    """incoming traceparent 헤더가 있으면 서버 span 이 같은 trace_id 로 연결된다.

    Spring → AI 서비스 분산 트레이스 연결의 핵심: 서버 span 이 들어온 traceparent
    를 부모로 채택해야 한다(traceparent 추출 체인).
    """
    exporter = sdk_provider
    app = _make_app()
    FastAPIInstrumentor.instrument_app(app)

    # W3C traceparent: version-traceid(32hex)-spanid(16hex)-flags
    trace_id_hex = "0af7651916cd43dd8448eb211c80319c"
    parent_span_hex = "b7ad6b7169203331"
    traceparent = f"00-{trace_id_hex}-{parent_span_hex}-01".encode()
    try:
        asyncio.run(
            _asgi_get(app, "/ping", headers=[(b"traceparent", traceparent)])
        )
        spans = {s.name: s for s in exporter.get_finished_spans()}
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    server = spans["GET /ping"]
    assert format(server.context.trace_id, "032x") == trace_id_hex, (
        "서버 span 이 들어온 traceparent 의 trace_id 를 상속하지 않았다(트레이스 단절)."
    )
    assert server.parent is not None
    assert format(server.parent.span_id, "016x") == parent_span_hex, (
        "서버 span 의 부모가 들어온 traceparent span 이 아니다."
    )


def test_otel_middleware_is_outermost_and_preserves_500_and_process_time(sdk_provider):
    """OTel 미들웨어가 최외곽(traceparent 최우선) + 500 변환·ProcessTime 헤더 보존.

    - /boom 의 미처리 예외가 500 JSON 으로 변환된다(미들웨어 체인 보존).
    - ProcessTimeMiddleware 의 X-Process-Time 헤더가 응답에 존재한다.
    - 그럼에도 서버 span 은 정상 생성된다(미들웨어 체인이 OTel 안쪽에서 동작).
    """
    exporter = sdk_provider
    app = _make_app()
    FastAPIInstrumentor.instrument_app(app)
    try:
        ok = asyncio.run(_asgi_get(app, "/ping"))
        err = asyncio.run(_asgi_get(app, "/boom"))
        names = [s.name for s in exporter.get_finished_spans()]
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    # ProcessTimeMiddleware 헤더 보존(정상 응답).
    assert b"x-process-time" in ok["headers"], (
        f"ProcessTime 헤더 누락 — 미들웨어 체인 손상. headers={ok['headers']}"
    )
    assert ok["status"] == 200
    # 미처리 예외의 500 변환 보존(미들웨어 체인이 OTel 안쪽에서 정상 동작).
    assert err["status"] == 500
    # 두 요청 모두 서버 span 생성(OTel 이 최외곽이라 예외 경로도 계측).
    assert names.count("GET /ping") == 1
    assert names.count("GET /boom") == 1


# ---------------------------------------------------------------------------
# 엔드투엔드 와이어링 회귀 가드 — main 을 OTel 활성으로 fresh import 한 뒤
# 실제 lifespan + 요청을 거쳐 서버 span 이 emit 되는지 검증한다.
#
# 이 테스트가 setup_telemetry 호출이 *모듈 레벨* 에 있어야만 통과한다:
#   - 모듈 레벨 호출(수정 후): import 시 instrument → 이후 lifespan/요청 때
#     스택이 빌드되며 OTel 미들웨어 포함 ⇒ 서버 span 생성.
#   - lifespan 호출(수정 전): import~startup 사이 스택이 빌드·캐시된 뒤 lifespan
#     본문에서 instrument ⇒ 캐시된 스택에 OTel 미들웨어 없음 ⇒ 서버 span 미생성.
# 즉 setup_telemetry 를 lifespan 으로 되돌리면(=sabotage) 이 테스트가 깨진다.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_app_e2e_emits_server_span_with_otel_enabled(sdk_provider):
    """main 을 OTel 활성으로 fresh import → 실제 lifespan + 요청에서 서버 span emit.

    setup_telemetry 가 모듈 레벨(스택 빌드 전)에서 instrument_app 을 호출해야
    스택에 OTel ASGI 미들웨어가 포함되어 서버 span 이 생성된다. 호출이 lifespan
    으로 되돌아가면(스택 캐시 후) 서버 span 이 사라져 이 테스트가 실패한다.
    """
    import importlib
    import sys
    from contextlib import ExitStack
    from unittest.mock import MagicMock, patch

    from httpx import ASGITransport, AsyncClient

    import core.telemetry as telemetry

    exporter = sdk_provider

    # OTel 활성 설정. 단, exporter/provider 생성은 모킹해 실 전송·전역 provider
    # override 를 막고, FastAPIInstrumentor.instrument_app 만 실제로 실행시킨다.
    enabled = MagicMock()
    for k, v in {
        "otel_enabled": True,
        "otel_service_name": "on-seoul-agent",
        "otel_exporter_otlp_endpoint": "http://signoz:4317",
        "app_version": "0.1.0",
        "otel_environment": "test",
        "otel_exporter_otlp_timeout": 5,
        "otel_metric_export_interval_ms": 60000,
    }.items():
        setattr(enabled, k, v)

    # exporter/provider/지표/로그 SDK 진입점 + 부작용 instrumentor 를 모킹한다.
    # FastAPIInstrumentor 는 실제로 실행시켜 서버 span 생성을 검증한다.
    _telemetry_mocks = [
        "OTLPSpanExporter",
        "OTLPMetricExporter",
        "OTLPLogExporter",
        "TracerProvider",
        "MeterProvider",
        "LoggerProvider",
        "BatchSpanProcessor",
        "BatchLogRecordProcessor",
        "PeriodicExportingMetricReader",
        "LoggingHandler",
        "set_tracer_provider",
        "set_meter_provider",
        "set_logger_provider",
        "HTTPXClientInstrumentor",
        "RedisInstrumentor",
        "SQLAlchemyInstrumentor",
    ]

    saved_main = sys.modules.pop("main", None)
    # 깨끗한 instrument 상태 보장(이전 테스트 잔여 instrument 가드 제거).
    telemetry._PROVIDERS.clear()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(telemetry, "settings", enabled))
            for name in _telemetry_mocks:
                stack.enter_context(patch.object(telemetry, name))

            main_mod = importlib.import_module("main")
            app = main_mod.app
            instrumented = getattr(app, "_is_instrumented_by_opentelemetry", False)
            assert instrumented, (
                "import 후 app 이 instrument 되지 않았다 — setup_telemetry 가 모듈"
                " 레벨에서 실행되지 않음(또는 OTel 비활성)."
            )

            try:
                # 실제 lifespan 을 거쳐 요청 — Redis/그래프 초기화는 mock.
                stack.enter_context(
                    patch.object(main_mod, "get_redis", return_value=MagicMock())
                )
                stack.enter_context(
                    patch.object(main_mod, "AgentGraph", return_value=MagicMock())
                )
                stack.enter_context(patch.object(main_mod, "init_langfuse"))
                stack.enter_context(patch.object(main_mod, "init_global_sema"))
                stack.enter_context(patch.object(main_mod, "init_openai_http_client"))

                transport = ASGITransport(app=app)
                async with AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    resp = await client.get("/health")
            finally:
                FastAPIInstrumentor.uninstrument_app(app)

        assert resp.status_code == 200
        names = [s.name for s in exporter.get_finished_spans()]
        assert "GET /health" in names, (
            "OTel 활성 + 모듈 레벨 instrument 인데 서버 span 미생성 — "
            f"setup_telemetry 가 lifespan 으로 회귀했을 수 있다. spans={names}"
        )
    finally:
        telemetry._PROVIDERS.clear()
        if saved_main is not None:
            sys.modules["main"] = saved_main
        else:
            sys.modules.pop("main", None)
