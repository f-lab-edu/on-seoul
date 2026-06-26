"""POST /chat/stream OTel 트레이스 전파 단위 테스트.

검증 대상:
- _stream 이 parent_ctx 를 attach 한 뒤 명시 span(chat_stream.workflow) 으로
  graph.stream 루프를 감싸므로, 스트리밍 중 생성된 자식 span 이 부모 span 과
  같은 trace 에 chat_stream.workflow 하위로 연결된다(트레이스 단절 해소).
- OTel 비활성(no-op tracer) 시 attach/detach/start_as_current_span 가 동작·SSE
  출력을 바꾸지 않는다.

실제 exporter 전송 없이 InMemorySpanExporter 로 검증한다. LLM/DB 호출 없음.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

import routers.chat as chat
from core.exceptions import RateLimitException
from routers.chat import _stream
from schemas.chat import ChatRequest


def _request() -> ChatRequest:
    return ChatRequest(room_id=1, message_id=1, message="수영장 알려줘")


def _make_graph_with_child_span(tracer):
    """graph.stream 을 모사한다. 루프 본문에서 자식 span 을 1개 생성한다.

    실제로는 httpx/SQLAlchemy Instrumentor 가 만드는 span 에 해당한다 —
    현재 활성 컨텍스트를 부모로 잡는지(= chat_stream.workflow 하위) 검증한다.
    """
    graph = MagicMock()

    async def _gen(state, **kwargs):
        with tracer.start_as_current_span("child.work"):
            yield "progress", {"step": "routing", "message": "..."}
        # result 프레임 — 최소 페이로드.
        yield "result", {"message_id": 1, "plan": {}, "output": {}}

    graph.stream = _gen
    return graph


def _parse_sse(frames: list[bytes]) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for chunk in frames:
        for line in chunk.decode().splitlines():
            if line.startswith("event: "):
                current["event"] = line[len("event: ") :]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[len("data: ") :])
            elif line == "" and current:
                events.append(current)
                current = {}
    if current:
        events.append(current)
    return events


@pytest.fixture()
def in_memory_tracer():
    """SDK TracerProvider + InMemorySpanExporter 를 모듈 tracer 에 주입한다.

    routers.chat._tracer 를 SDK tracer 로 교체하고, _stream 내부에서 쓰는
    start_as_current_span 도 동일 provider 를 사용하도록 한다. 테스트 종료 후
    원복한다(전역 provider 는 건드리지 않는다).
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with patch.object(chat, "_tracer", tracer):
        yield tracer, exporter
    exporter.clear()


async def test_child_spans_linked_under_workflow_span(in_memory_tracer):
    """스트리밍 중 생성된 자식 span 이 chat_stream.workflow 하위·같은 trace 에 연결된다."""
    tracer, exporter = in_memory_tracer

    # 핸들러의 서버 span 활성 시점을 모사 — 부모 span 컨텍스트를 캡처한다.
    with tracer.start_as_current_span("server.POST /chat/stream"):
        parent_ctx = otel_context.get_current()
        server_span = trace.get_current_span()
        server_ctx = server_span.get_span_context()

    graph = _make_graph_with_child_span(tracer)

    frames = [f async for f in _stream(_request(), graph, parent_ctx)]

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "chat_stream.workflow" in spans
    assert "child.work" in spans

    workflow = spans["chat_stream.workflow"]
    child = spans["child.work"]

    # 모든 span 이 서버 span 과 동일 trace 다(= 단절 해소).
    assert workflow.context.trace_id == server_ctx.trace_id
    assert child.context.trace_id == server_ctx.trace_id

    # 계층: server -> chat_stream.workflow -> child.work
    assert workflow.parent is not None
    assert workflow.parent.span_id == server_ctx.span_id
    assert child.parent is not None
    assert child.parent.span_id == workflow.context.span_id

    # SSE 동작 보존 — progress + final 발행.
    events = _parse_sse(frames)
    assert [e["event"] for e in events] == ["progress", "final"]


async def test_parallel_gather_tasks_share_workflow_trace(in_memory_tracer):
    """asyncio.gather 병렬 task 의 span 도 chat_stream.workflow 하위·같은 trace 다.

    VectorAgent 가 retrieval 채널을 asyncio.gather 로 병렬 실행할 때 각 task 가
    만드는 자식 span(httpx 등)이 contextvars 복사를 통해 workflow span 컨텍스트를
    상속하는지 검증한다(트레이스 단절이 병렬 경로까지 해소됨).
    """
    tracer, exporter = in_memory_tracer

    with tracer.start_as_current_span("server.POST /chat/stream"):
        parent_ctx = otel_context.get_current()
        server_ctx = trace.get_current_span().get_span_context()

    graph = MagicMock()

    async def _gen(state, **kwargs):
        async def _task(name):
            with tracer.start_as_current_span(name):
                await asyncio.sleep(0)

        await asyncio.gather(_task("vec.a"), _task("vec.b"))
        yield "result", {"message_id": 1, "plan": {}, "output": {}}

    graph.stream = _gen

    _ = [f async for f in _stream(_request(), graph, parent_ctx)]

    spans = {s.name: s for s in exporter.get_finished_spans()}
    workflow = spans["chat_stream.workflow"]
    for name in ("vec.a", "vec.b"):
        s = spans[name]
        assert s.context.trace_id == server_ctx.trace_id
        assert s.parent is not None
        assert s.parent.span_id == workflow.context.span_id


class _AttachDetachSpy:
    """routers.chat 가 호출하는 otel_context.attach/detach 를 가로채 토큰 페어링을 본다.

    detach 가 누락되면(컨텍스트 토큰 누수) 단언이 즉시 잡아낸다. get_current()
    비교는 동일 컨텍스트 재attach 시 idempotent 해서 누수를 못 잡으므로, attach
    가 만든 토큰이 모두 detach 되는지를 직접 검증한다.
    """

    def __init__(self):
        self.attached: list = []
        self.detached: list = []

    def attach(self, ctx):
        token = otel_context.attach(ctx)
        self.attached.append(token)
        return token

    def detach(self, token):
        self.detached.append(token)
        return otel_context.detach(token)

    def assert_all_detached(self):
        assert self.attached, "attach 가 한 번도 호출되지 않았다"
        assert self.detached == self.attached, (
            f"detach 누락(컨텍스트 토큰 누수): attached={len(self.attached)} "
            f"detached={len(self.detached)}"
        )


async def _run_with_spy(graph, tracer):
    spy = _AttachDetachSpy()
    with tracer.start_as_current_span("server"):
        parent_ctx = otel_context.get_current()
        mock_ctx = MagicMock(wraps=otel_context)
        mock_ctx.attach.side_effect = spy.attach
        mock_ctx.detach.side_effect = spy.detach
        with patch.object(chat, "otel_context", mock_ctx):
            frames = [f async for f in _stream(_request(), graph, parent_ctx)]
    return spy, frames


async def test_detach_called_after_normal_completion(in_memory_tracer):
    """정상 완료 시 attach 한 토큰이 detach 되어 누수가 없다."""
    tracer, _ = in_memory_tracer
    graph = _make_graph_with_child_span(tracer)
    spy, frames = await _run_with_spy(graph, tracer)
    spy.assert_all_detached()
    assert [e["event"] for e in _parse_sse(frames)] == ["progress", "final"]


async def test_detach_called_on_rate_limit(in_memory_tracer):
    """RateLimitException 경로(조기 return)에서도 detach 가 실행된다."""
    tracer, _ = in_memory_tracer
    graph = MagicMock()

    async def _gen(state, **kwargs):
        raise RateLimitException("embed rate limit")
        yield  # pragma: no cover

    graph.stream = _gen
    spy, frames = await _run_with_spy(graph, tracer)
    spy.assert_all_detached()
    assert [e["event"] for e in _parse_sse(frames)] == ["error"]


async def test_detach_called_on_generic_exception(in_memory_tracer):
    """일반 예외 경로(조기 return)에서도 detach 가 실행돼 토큰 누수가 없다."""
    tracer, _ = in_memory_tracer
    graph = MagicMock()

    async def _gen(state, **kwargs):
        raise ValueError("db down")
        yield  # pragma: no cover

    graph.stream = _gen
    spy, frames = await _run_with_spy(graph, tracer)
    spy.assert_all_detached()
    assert [e["event"] for e in _parse_sse(frames)] == ["error"]


async def test_noop_tracer_preserves_sse_and_does_not_crash():
    """OTel 비활성(no-op tracer) + parent_ctx=None 에서도 SSE 동작 불변."""
    # 모듈 기본 _tracer 는 provider 미등록 시 no-op tracer 다. 명시적으로
    # 글로벌 tracer 를 사용하되 SDK provider 를 등록하지 않는다.
    graph = MagicMock()

    async def _gen(state, **kwargs):
        yield "progress", {"step": "routing", "message": "..."}
        yield "result", {"message_id": 1, "plan": {}, "output": {}}

    graph.stream = _gen

    # parent_ctx=None 이면 attach/detach 를 건너뛴다(불필요한 컨텍스트 조작 없음).
    mock_ctx = MagicMock(wraps=otel_context)
    with patch.object(chat, "otel_context", mock_ctx):
        frames = [f async for f in _stream(_request(), graph, None)]
    mock_ctx.attach.assert_not_called()
    mock_ctx.detach.assert_not_called()

    events = _parse_sse(frames)
    assert [e["event"] for e in events] == ["progress", "final"]
    assert events[-1]["data"]["message_id"] == 1
