"""POST /embeddings/services/sync OTel 트레이스 전파 단위 테스트.

검증 대상:
- services_sync 핸들러가 현재 OTel 컨텍스트(parent_ctx)를 캡처해
  background.add_task 로 _run_services_sync 에 전달한다.
- _run_services_sync 가 parent_ctx 를 attach 한 뒤 명시 span
  (embeddings.sync.workflow) 으로 백그라운드 작업을 감싸므로, 작업 중 생성된
  자식 span(httpx/SQLAlchemy 등)이 서버 span 과 같은 trace 에 workflow 하위로
  연결된다(트레이스 단절 해소).
- OTel 비활성(no-op tracer) + parent_ctx=None 에서도 attach/detach 가 호출되지
  않고 동작 불변이다.

실제 exporter 전송 없이 InMemorySpanExporter 로 검증한다. LLM/DB 호출 없음 —
엔진 생성/dispose, 임베딩, process_service 는 모두 패치한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import routers.embeddings as embeddings
from routers.embeddings import _run_services_sync, services_sync
from schemas.embeddings import ServiceEmbeddingsSyncRequest


@pytest.fixture()
def in_memory_tracer():
    """SDK TracerProvider + InMemorySpanExporter 를 모듈 tracer 에 주입한다.

    routers.embeddings._tracer 를 SDK tracer 로 교체한다. 테스트 종료 후 원복하며
    전역 provider 는 건드리지 않는다.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with patch.object(embeddings, "_tracer", tracer):
        yield tracer, exporter
    exporter.clear()


@pytest.fixture()
def patched_engines():
    """create_async_engine / dispose 를 패치해 실제 DB 접속을 막는다."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    with (
        patch.object(embeddings, "create_async_engine", return_value=engine),
        patch.object(embeddings, "async_sessionmaker", return_value=MagicMock()),
        patch.object(embeddings, "get_embeddings", return_value=MagicMock()),
        patch.object(embeddings, "get_chat_model", return_value=MagicMock()),
    ):
        yield engine


async def test_services_sync_captures_and_passes_parent_ctx():
    """핸들러가 parent_ctx 를 캡처해 background task 인자로 전달한다."""
    background = MagicMock()
    req = ServiceEmbeddingsSyncRequest(upsert=["a", "b"], delete=["c"])

    sentinel_ctx = object()
    mock_ctx = MagicMock(wraps=otel_context)
    mock_ctx.get_current.return_value = sentinel_ctx
    with patch.object(embeddings, "otel_context", mock_ctx):
        resp = await services_sync(req, background)

    mock_ctx.get_current.assert_called_once()
    background.add_task.assert_called_once_with(
        _run_services_sync, ["a", "b"], ["c"], sentinel_ctx
    )
    assert resp.accepted == {"upsert": 2, "delete": 1}


async def test_child_spans_linked_under_workflow_span(
    in_memory_tracer, patched_engines
):
    """백그라운드 작업 중 생성된 자식 span 이 workflow span 하위·같은 trace 에 연결된다."""
    tracer, exporter = in_memory_tracer

    # 핸들러의 서버 span 활성 시점을 모사 — 부모 span 컨텍스트를 캡처한다.
    with tracer.start_as_current_span("server.POST /embeddings/services/sync"):
        parent_ctx = otel_context.get_current()
        server_ctx = trace.get_current_span().get_span_context()

    async def _fake_process(*args, **kwargs):
        # process_service 가 만드는 내부 span(httpx/SQLAlchemy)을 모사한다.
        with tracer.start_as_current_span("child.embed"):
            pass

    with (
        patch.object(embeddings, "process_service", side_effect=_fake_process),
        patch.object(
            embeddings, "_fetch_service_row", AsyncMock(return_value={"service_id": "a"})
        ),
    ):
        await _run_services_sync(["a"], [], parent_ctx)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "embeddings.sync.workflow" in spans
    assert "child.embed" in spans

    workflow = spans["embeddings.sync.workflow"]
    child = spans["child.embed"]

    assert workflow.context.trace_id == server_ctx.trace_id
    assert child.context.trace_id == server_ctx.trace_id

    assert workflow.parent is not None
    assert workflow.parent.span_id == server_ctx.span_id
    assert child.parent is not None
    assert child.parent.span_id == workflow.context.span_id

    # 식별 속성 검증.
    assert workflow.attributes["embeddings.upsert_count"] == 1
    assert workflow.attributes["embeddings.delete_count"] == 0


async def test_noop_tracer_and_none_ctx_does_not_attach(
    in_memory_tracer, patched_engines
):
    """parent_ctx=None 이면 attach/detach 를 호출하지 않고 동작 불변이다."""
    _, exporter = in_memory_tracer

    with patch.object(embeddings, "otel_context", wraps=otel_context) as mock_ctx:
        await _run_services_sync([], [], None)

    mock_ctx.attach.assert_not_called()
    mock_ctx.detach.assert_not_called()
    # workflow span 은 생성되지만(no-op tracer 무관), 자식/외부 호출 없음.
    spans = {s.name for s in exporter.get_finished_spans()}
    assert "embeddings.sync.workflow" in spans


async def test_detach_called_after_completion(in_memory_tracer, patched_engines):
    """parent_ctx 가 주어지면 attach 한 토큰이 finally 에서 detach 된다(누수 없음)."""
    tracer, _ = in_memory_tracer
    with tracer.start_as_current_span("server"):
        parent_ctx = otel_context.get_current()

    attached: list = []
    detached: list = []

    def _attach(ctx):
        token = otel_context.attach(ctx)
        attached.append(token)
        return token

    def _detach(token):
        detached.append(token)
        return otel_context.detach(token)

    mock_ctx = MagicMock(wraps=otel_context)
    mock_ctx.attach.side_effect = _attach
    mock_ctx.detach.side_effect = _detach
    with patch.object(embeddings, "otel_context", mock_ctx):
        await _run_services_sync([], [], parent_ctx)

    assert attached, "attach 가 호출되지 않았다"
    assert detached == attached, "detach 누락(컨텍스트 토큰 누수)"
