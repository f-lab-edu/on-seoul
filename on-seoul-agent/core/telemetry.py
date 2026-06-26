"""OpenTelemetry 인프라 계측 초기화 (SigNoz, OTLP gRPC 4317).

설계 원칙
- 인프라 계측만: FastAPI(서버 span) / httpx(외부 HTTP·Gemini) / SQLAlchemy(asyncpg)
  / redis. LLM·LangChain 전용 계측은 넣지 않는다 — Langfuse가 별개 파이프라인으로
  LLM I/O·토큰·비용을 담당한다(core/langfuse_client.py). 이 모듈은 인프라 계측 전용.
- 기존 커스텀 트레이싱(chat_agent_traces, agents/graph.py·nodes.py)과 병행한다.
- fail-open: exporter 연결/instrument 실패가 앱 기동·요청을 막지 않는다.
- 토글: settings.otel_enabled=False(기본) 또는 endpoint 미설정 시 완전 no-op.

infra 핸드오프(컨테이너 주입 env)는 core/config.py 의 OTel 섹션 주석 참조.
프로그래밍 방식 초기화이므로 Dockerfile ENTRYPOINT(opentelemetry-instrument 래핑)는
변경할 필요가 없다.
"""

import logging

from fastapi import FastAPI

# --- OTel SDK / API (모듈 속성으로 노출 → 테스트에서 patch.object 가능) ---
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import set_tracer_provider

from core.config import settings

logger = logging.getLogger(__name__)

# shutdown 시 flush 대상 provider 보관. (tracer, meter, logger)
_PROVIDERS: list[object] = []
# logging 루트에 부착한 핸들러 — shutdown 시 제거.
_LOG_HANDLER: LoggingHandler | None = None


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.otel_environment,
        }
    )


def _setup_traces(resource: Resource) -> None:
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,
        timeout=settings.otel_exporter_otlp_timeout,
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    set_tracer_provider(provider)
    _PROVIDERS.append(provider)


def _setup_metrics(resource: Resource) -> None:
    exporter = OTLPMetricExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,
        timeout=settings.otel_exporter_otlp_timeout,
    )
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=settings.otel_metric_export_interval_ms,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    set_meter_provider(provider)
    _PROVIDERS.append(provider)


def _setup_logs(resource: Resource) -> None:
    global _LOG_HANDLER
    exporter = OTLPLogExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,
        timeout=settings.otel_exporter_otlp_timeout,
    )
    provider = LoggerProvider(resource=resource)
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)
    _PROVIDERS.append(provider)

    # Python logging → OTel 브리지. 루트에 부착해 앱 로그를 logs 시그널로 전송.
    _LOG_HANDLER = LoggingHandler(level=logging.NOTSET, logger_provider=provider)
    logging.getLogger().addHandler(_LOG_HANDLER)


def _setup_instrumentors(app: FastAPI) -> None:
    # FastAPI 서버 요청 span. instrument_app 은 app 생성 후 호출해야 한다.
    # SSE(/chat/stream) StreamingResponse 는 ASGI http.response.body 이벤트를
    # 스트리밍하며, FastAPIInstrumentor 는 응답을 버퍼링하지 않고 send 이벤트를
    # 패스스루로 계측하므로 스트리밍을 깨지 않는다.
    # NOTE: 단, StreamingResponse 의 제너레이터 바디는 서버 span 활성 컨텍스트 밖에서
    #   실행되므로, /chat/stream 의 에이전트 작업 span 을 서버 span 하위로 잇기 위해
    #   routers/chat.py 가 서버 컨텍스트를 캡처해 제너레이터에 수동 재부착한다
    #   (chat_stream → _stream(parent_ctx) → otel_context.attach + chat_stream.workflow span).
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()

    # SQLAlchemy async 엔진은 sync_engine 을 instrument 한다. 두 DB 엔진 각각 계측.
    # 함수 내부 지연 import: core.database 가 import 시점에 엔진을 생성하므로,
    # 모듈 상단 import 시 telemetry import 순서/사이클에 묶이는 것을 피한다.
    from core.database import _on_ai_engine, _on_data_engine

    sa = SQLAlchemyInstrumentor()
    sa.instrument(engine=_on_ai_engine.sync_engine)
    sa.instrument(engine=_on_data_engine.sync_engine)


def setup_telemetry(app: FastAPI) -> bool:
    """OTel providers 등록 + 인프라 Instrumentor 부착.

    Returns:
        계측이 실제로 활성화되면 True, no-op(비활성/실패)이면 False.
    """
    if not settings.otel_enabled or not settings.otel_exporter_otlp_endpoint:
        logger.info("OTel 비활성 — 계측을 건너뜁니다 (otel_enabled/endpoint 확인).")
        return False

    # idempotency 가드: 이미 활성(provider 등록됨)이면 재초기화하지 않는다.
    # lifespan 1회 호출이 정상이나, 재진입 시 provider/handler 중복 등록을 방지.
    if _PROVIDERS:
        logger.info("OTel 이미 활성 — 중복 초기화를 건너뜁니다.")
        return True

    try:
        resource = _build_resource()
        _setup_traces(resource)
        _setup_metrics(resource)
        _setup_logs(resource)
        _setup_instrumentors(app)
    except Exception:
        # fail-open: 계측 초기화 실패가 앱 기동을 막아서는 안 된다.
        logger.warning("OTel 초기화 실패 — 계측 없이 계속 진행합니다.", exc_info=True)
        return False

    logger.info(
        "OTel 활성 — endpoint=%s service=%s env=%s",
        settings.otel_exporter_otlp_endpoint,
        settings.otel_service_name,
        settings.otel_environment,
    )
    return True


def shutdown_telemetry() -> None:
    """provider flush/shutdown. lifespan 종료 시 호출 (best-effort)."""
    global _LOG_HANDLER
    if _LOG_HANDLER is not None:
        try:
            logging.getLogger().removeHandler(_LOG_HANDLER)
        except Exception:
            pass
        _LOG_HANDLER = None

    for provider in _PROVIDERS:
        shutdown = getattr(provider, "shutdown", None)
        if shutdown is None:
            continue
        try:
            shutdown()
        except Exception:
            logger.warning("OTel provider shutdown 실패", exc_info=True)
    _PROVIDERS.clear()
