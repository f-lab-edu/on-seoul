"""AgentGraph run()/stream() 의 Langfuse Option 2(enclosing span) 검증.

핵심 보증:
- client/handler 가 None 이면 run/stream config 에 callbacks/metadata 가 들어가지 않고
  enclosing span 도 진입하지 않는다 (회귀 금지 — 기존 동작 100% 동일).
- client+handler 활성(mock) 시:
    * client.start_as_current_observation(as_type="span", name="chat") 진입,
    * propagate_attributes(trace_name="chat", session_id=room_id) 진입,
    * config 에 callbacks=[handler] + metadata(message_id) 주입,
    * 완료 후 root span 에 output=answer + metadata(intent/action/node_path/...) 갱신.
- compiled graph 의 ainvoke/astream 을 모킹해 실제 노드 실행 없이 호출 인자만 캡처한다.
"""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.graph import AgentGraph, _trace_completion_metadata
from tests.helpers import make_agent_state


def _graph_with_mocked_compiled():
    """compiled graph 의 ainvoke/astream 을 모킹한 AgentGraph 를 만든다."""
    graph = AgentGraph()
    graph._compiled_graph = MagicMock()
    return graph


def _make_langfuse_mocks():
    """enclosing span(Option 2) 진입을 추적하는 mock client + span + propagate.

    Returns (client, span, propagate_mock, propagate_calls).
    """
    span = MagicMock(name="root_span")

    client = MagicMock(name="langfuse_client")

    @contextmanager
    def _start_obs(*args, **kwargs):
        client.start_as_current_observation.call_kwargs = kwargs
        yield span

    client.start_as_current_observation = MagicMock(side_effect=_start_obs)
    client.start_as_current_observation.call_kwargs = None

    propagate_calls: list[dict] = []

    @contextmanager
    def _propagate(**kwargs):
        propagate_calls.append(kwargs)
        yield None

    return client, span, _propagate, propagate_calls


async def _drain_stream(graph, state):
    async for _ in graph.stream(state):
        pass


# ── 비활성(회귀 금지): client/handler None → 기존과 100% 동일 ──


@pytest.mark.asyncio
async def test_run_no_handler_omits_callbacks_and_span():
    """핸들러/클라이언트 None → config 에 callbacks/metadata 없음, span 미진입."""
    graph = _graph_with_mocked_compiled()
    captured = {}

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=None),
        patch("core.langfuse_client.get_langfuse_client", return_value=None),
    ):
        await graph.run(make_agent_state(room_id=7, message_id=42))

    cfg = captured["config"]
    assert cfg == {"recursion_limit": 50}
    assert "callbacks" not in cfg
    assert "metadata" not in cfg


@pytest.mark.asyncio
async def test_stream_no_handler_omits_callbacks_and_span():
    """stream 경로도 핸들러 None 시 callbacks/metadata 없음 (회귀 금지)."""
    graph = _graph_with_mocked_compiled()
    captured = {}

    async def _astream(state, stream_mode=None, config=None):
        captured["config"] = config
        yield "values", dict(state)

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=None),
        patch("core.langfuse_client.get_langfuse_client", return_value=None),
    ):
        await _drain_stream(graph, make_agent_state(room_id=7, message_id=42))

    cfg = captured["config"]
    assert cfg == {"recursion_limit": 50}
    assert "callbacks" not in cfg
    assert "metadata" not in cfg


# ── 활성(Option 2): enclosing span + propagate + callbacks + post-hoc metadata ──


@pytest.mark.asyncio
async def test_run_with_langfuse_enters_span_and_propagates():
    """run 활성 → span 진입(name=chat) + propagate(trace_name/session_id)
    + callbacks=[handler] + metadata(message_id) + 완료 후 output/metadata 갱신."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client, span, propagate_cm, propagate_calls = _make_langfuse_mocks()

    result_state = make_agent_state(room_id=7, message_id=42)
    result_state["output"] = {"answer": "최종 답변입니다."}
    result_state["plan"] = {"intent": MagicMock(value="VECTOR_SEARCH")}
    result_state["triage"] = {"action": MagicMock(value="RETRIEVE")}
    result_state["node_path"] = ["intake_node", "router_node", "answer_node"]
    result_state["retry_count"] = 1
    result_state["retry_relaxed"] = True
    result_state["cache_hit"] = False
    result_state["error"] = None

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return result_state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        await graph.run(make_agent_state(room_id=7, message_id=42))

    # enclosing span 진입
    span_kwargs = client.start_as_current_observation.call_kwargs
    assert span_kwargs["as_type"] == "span"
    assert span_kwargs["name"] == "chat"
    # 진입 시 input=사용자 메시지
    assert span_kwargs["input"] == make_agent_state(room_id=7, message_id=42)["message"]

    # propagate_attributes(trace_name="chat", session_id="7")
    assert len(propagate_calls) == 1
    pa = propagate_calls[0]
    assert pa["trace_name"] == "chat"
    assert pa["session_id"] == "7"

    # config callbacks/metadata
    cfg = captured["config"]
    assert cfg["recursion_limit"] == 50
    assert cfg["callbacks"] == [handler]
    assert cfg["metadata"]["message_id"] == 42

    # 완료 후 root span 갱신: output=answer + metadata
    span.update.assert_called_once()
    upd = span.update.call_args.kwargs
    assert upd["output"] == "최종 답변입니다."
    meta = upd["metadata"]
    assert meta["intent"] == "VECTOR_SEARCH"
    assert meta["action"] == "RETRIEVE"
    assert meta["node_path"] == ["intake_node", "router_node", "answer_node"]
    assert meta["retry_count"] == 1
    assert meta["retry_relaxed"] is True
    assert meta["cache_hit"] is False
    assert meta["error"] is None


@pytest.mark.asyncio
async def test_stream_with_langfuse_keeps_span_over_loop():
    """stream 활성 → span 진입 + propagate + callbacks, astream 루프 종료 후 갱신."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client, span, propagate_cm, propagate_calls = _make_langfuse_mocks()

    final_values = make_agent_state(room_id=9, message_id=99)
    final_values["output"] = {"answer": "스트림 답변"}
    final_values["plan"] = {"intent": MagicMock(value="SQL_SEARCH")}
    final_values["triage"] = {"action": MagicMock(value="RETRIEVE")}
    final_values["node_path"] = ["intake_node", "answer_node"]
    final_values["retry_count"] = 0
    final_values["retry_relaxed"] = False
    final_values["cache_hit"] = True
    final_values["error"] = None

    async def _astream(state, stream_mode=None, config=None):
        captured["config"] = config
        yield "values", final_values

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        await _drain_stream(graph, make_agent_state(room_id=9, message_id=99))

    span_kwargs = client.start_as_current_observation.call_kwargs
    assert span_kwargs["name"] == "chat"
    assert len(propagate_calls) == 1
    assert propagate_calls[0]["session_id"] == "9"

    cfg = captured["config"]
    assert cfg["callbacks"] == [handler]
    assert cfg["metadata"]["message_id"] == 99

    span.update.assert_called_once()
    upd = span.update.call_args.kwargs
    assert upd["output"] == "스트림 답변"
    assert upd["metadata"]["intent"] == "SQL_SEARCH"
    assert upd["metadata"]["cache_hit"] is True


# ── 완료 메타데이터 추출의 엣지: enum None / error / 빈 슬롯 ──


def test_completion_metadata_none_intent_action_safe():
    """intent/action 미설정(plan/triage 빈 dict) → .value 접근 없이 None 직렬화.

    enum 이 없을 때 `intent.value` AttributeError 가 나지 않아야 한다(커버리지 갭).
    """
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["intent"] is None
    assert meta["action"] is None
    # 평면 기본 슬롯도 안전하게 추출.
    assert meta["retry_count"] == 0
    assert meta["retry_relaxed"] is False
    assert meta["cache_hit"] is False
    assert meta["error"] is None
    assert meta["node_path"] == []


def test_completion_metadata_error_present():
    """error 슬롯이 채워지면 metadata.error 로 그대로 노출된다."""
    state = make_agent_state(error="downstream LLM 500")
    meta = _trace_completion_metadata(state)
    assert meta["error"] == "downstream LLM 500"


def test_completion_metadata_only_intent_set():
    """intent 만 있고 action 은 없을 때 — 한쪽만 .value 직렬화."""
    state = make_agent_state()
    state["plan"] = {"intent": MagicMock(value="MAP")}
    state["triage"] = {}
    meta = _trace_completion_metadata(state)
    assert meta["intent"] == "MAP"
    assert meta["action"] is None


@pytest.mark.asyncio
async def test_run_with_langfuse_room_id_none_coerces_session():
    """room_id 가 None 이어도 session_id=str(None)='None' 으로 안전하게 진입(크래시 금지)."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client, span, propagate_cm, propagate_calls = _make_langfuse_mocks()

    result_state = make_agent_state(room_id=None, message_id=None)
    result_state["output"] = {"answer": "답"}

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return result_state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        await graph.run(make_agent_state(room_id=None, message_id=None))

    assert propagate_calls[0]["session_id"] == "None"
    assert captured["config"]["metadata"]["message_id"] is None
    # 완료 후 갱신은 정상 호출(엔티티 누락에도 크래시 없음).
    span.update.assert_called_once()


@pytest.mark.asyncio
async def test_stream_astream_error_exits_span_without_update():
    """astream 루프 중 예외 → span CM 이 깔끔히 종료되고(컨텍스트 누수 없음),
    완료-후 update 는 호출되지 않는다(비정상 종료 시 잘못된 output 미기록)."""
    graph = _graph_with_mocked_compiled()
    handler = MagicMock(name="handler")
    client, span, propagate_cm, propagate_calls = _make_langfuse_mocks()

    async def _astream(state, stream_mode=None, config=None):
        if False:
            yield  # pragma: no cover  - async generator 형태 유지
        raise RuntimeError("astream boom")

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        with pytest.raises(RuntimeError, match="astream boom"):
            await _drain_stream(graph, make_agent_state(room_id=5, message_id=5))

    # span 에 진입은 했으나(루프 진입 전), 비정상 종료라 완료-후 update 는 없다.
    assert client.start_as_current_observation.call_count == 1
    span.update.assert_not_called()


# ── 런타임 fail-open (SHOULD-FIX): 활성 분기 예외 시 비활성 폴백 ──


@pytest.mark.asyncio
async def test_run_span_enter_raises_falls_back_to_inactive():
    """활성 client.start_as_current_observation 가 예외 → 비활성 config 로 폴백,
    그래프는 정상 실행되어 result 를 반환한다(예외 전파 없음)."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client = MagicMock(name="langfuse_client")
    client.start_as_current_observation = MagicMock(side_effect=RuntimeError("span boom"))

    result_state = make_agent_state(room_id=7, message_id=42)
    result_state["output"] = {"answer": "정상 답변"}

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return result_state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
    ):
        result = await graph.run(make_agent_state(room_id=7, message_id=42))

    # 폴백: 비활성 config(callbacks/metadata 미부착), 그래프 결과는 정상.
    assert captured["config"] == {"recursion_limit": 50}
    assert result["output"]["answer"] == "정상 답변"


@pytest.mark.asyncio
async def test_stream_span_enter_raises_falls_back_to_inactive():
    """stream 활성 경로에서 span 진입 예외 → 비활성 폴백, 이벤트 정상 방출."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client = MagicMock(name="langfuse_client")
    client.start_as_current_observation = MagicMock(side_effect=RuntimeError("span boom"))

    final_values = make_agent_state(room_id=9, message_id=99)
    final_values["output"] = {"answer": "스트림 정상"}

    async def _astream(state, stream_mode=None, config=None):
        captured["config"] = config
        yield "values", final_values

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    events = []
    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
    ):
        async for evt in graph.stream(make_agent_state(room_id=9, message_id=99)):
            events.append(evt)

    assert captured["config"] == {"recursion_limit": 50}
    # 최종 result 이벤트가 정상 방출.
    kinds = [e[0] for e in events]
    assert "result" in kinds


@pytest.mark.asyncio
async def test_run_propagate_raises_falls_back_to_inactive():
    """propagate_attributes 가 예외 → 비활성 폴백, 그래프 정상 실행."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")
    client, span, _propagate_cm, _calls = _make_langfuse_mocks()

    @contextmanager
    def _boom(**kwargs):
        raise RuntimeError("propagate boom")
        yield  # pragma: no cover

    result_state = make_agent_state(room_id=7, message_id=42)
    result_state["output"] = {"answer": "정상 답변"}

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return result_state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", _boom),
    ):
        result = await graph.run(make_agent_state(room_id=7, message_id=42))

    assert captured["config"] == {"recursion_limit": 50}
    assert result["output"]["answer"] == "정상 답변"


@pytest.mark.asyncio
async def test_run_span_update_raises_does_not_propagate():
    """완료 후 root_span.update 가 예외 → best-effort, 그래프 결과는 정상 반환."""
    graph = _graph_with_mocked_compiled()
    handler = MagicMock(name="handler")
    client, span, propagate_cm, _calls = _make_langfuse_mocks()
    span.update = MagicMock(side_effect=RuntimeError("update boom"))

    result_state = make_agent_state(room_id=7, message_id=42)
    result_state["output"] = {"answer": "정상 답변"}

    async def _ainvoke(state, config=None):
        return result_state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        result = await graph.run(make_agent_state(room_id=7, message_id=42))

    assert result["output"]["answer"] == "정상 답변"
    span.update.assert_called_once()


@pytest.mark.asyncio
async def test_stream_span_update_raises_does_not_propagate():
    """stream 완료 후 root_span.update 예외 → 최종 SSE 이벤트 정상 방출."""
    graph = _graph_with_mocked_compiled()
    handler = MagicMock(name="handler")
    client, span, propagate_cm, _calls = _make_langfuse_mocks()
    span.update = MagicMock(side_effect=RuntimeError("update boom"))

    final_values = make_agent_state(room_id=9, message_id=99)
    final_values["output"] = {"answer": "스트림 정상"}

    async def _astream(state, stream_mode=None, config=None):
        yield "values", final_values

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    events = []
    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        async for evt in graph.stream(make_agent_state(room_id=9, message_id=99)):
            events.append(evt)

    kinds = [e[0] for e in events]
    assert "result" in kinds
    span.update.assert_called_once()


@pytest.mark.asyncio
async def test_run_graph_exception_still_propagates_under_active_span():
    """활성 span 경로에서 그래프 실행(ainvoke) 자체의 예외는 그대로 전파된다
    (Langfuse 폴백 try/except 가 본 기능의 정상 에러 흐름을 삼키지 않음)."""
    graph = _graph_with_mocked_compiled()
    handler = MagicMock(name="handler")
    client, span, propagate_cm, _calls = _make_langfuse_mocks()

    async def _ainvoke(state, config=None):
        raise ValueError("graph boom")

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with (
        patch("core.langfuse_client.get_langfuse_handler", return_value=handler),
        patch("core.langfuse_client.get_langfuse_client", return_value=client),
        patch("agents.graph.propagate_attributes", propagate_cm),
    ):
        with pytest.raises(ValueError, match="graph boom"):
            await graph.run(make_agent_state(room_id=7, message_id=42))

    # 그래프 예외라 완료-후 update 는 호출되지 않는다.
    span.update.assert_not_called()
