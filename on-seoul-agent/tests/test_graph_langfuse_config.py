"""AgentGraph run()/stream() 의 Langfuse config 조립 검증.

핵심 보증:
- get_langfuse_handler() 가 None 이면 run/stream config 에 callbacks/metadata 가 들어가지 않는다 (회귀 금지).
- 핸들러 활성(mock) 시 config 에 callbacks=[handler] + metadata(session=room_id, message_id) 주입.
- compiled graph 의 ainvoke/astream 을 모킹해 실제 노드 실행 없이 config 만 캡처한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.graph import AgentGraph
from tests.helpers import make_agent_state


def _graph_with_mocked_compiled():
    """compiled graph 의 ainvoke/astream 을 모킹한 AgentGraph 를 만든다."""
    graph = AgentGraph()
    graph._compiled_graph = MagicMock()
    return graph


async def _drain_stream(graph, state):
    async for _ in graph.stream(state):
        pass


@pytest.mark.asyncio
async def test_run_no_handler_omits_callbacks_and_metadata():
    """핸들러 None → config 에 callbacks/metadata 없음 (기존 동작 100% 동일)."""
    graph = _graph_with_mocked_compiled()
    captured = {}

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with patch("core.langfuse_client.get_langfuse_handler", return_value=None):
        await graph.run(make_agent_state(room_id=7, message_id=42))

    cfg = captured["config"]
    assert cfg == {"recursion_limit": 50}
    assert "callbacks" not in cfg
    assert "metadata" not in cfg


@pytest.mark.asyncio
async def test_run_with_handler_injects_callbacks_and_metadata():
    """핸들러 활성 → callbacks=[handler] + metadata(session=room_id, message_id)."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")

    async def _ainvoke(state, config=None):
        captured["config"] = config
        return state

    graph._compiled_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    with patch("core.langfuse_client.get_langfuse_handler", return_value=handler):
        await graph.run(make_agent_state(room_id=7, message_id=42))

    cfg = captured["config"]
    assert cfg["recursion_limit"] == 50
    assert cfg["callbacks"] == [handler]
    meta = cfg["metadata"]
    assert meta["langfuse_session_id"] == "7"
    assert meta["message_id"] == 42


@pytest.mark.asyncio
async def test_stream_no_handler_omits_callbacks_and_metadata():
    """stream 경로도 핸들러 None 시 callbacks/metadata 없음."""
    graph = _graph_with_mocked_compiled()
    captured = {}

    async def _astream(state, stream_mode=None, config=None):
        captured["config"] = config
        yield "values", dict(state)

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    with patch("core.langfuse_client.get_langfuse_handler", return_value=None):
        await _drain_stream(graph, make_agent_state(room_id=7, message_id=42))

    cfg = captured["config"]
    assert cfg == {"recursion_limit": 50}
    assert "callbacks" not in cfg
    assert "metadata" not in cfg


@pytest.mark.asyncio
async def test_stream_with_handler_injects_callbacks_and_metadata():
    """stream 경로도 핸들러 활성 시 callbacks=[handler] + metadata 주입."""
    graph = _graph_with_mocked_compiled()
    captured = {}
    handler = MagicMock(name="handler")

    async def _astream(state, stream_mode=None, config=None):
        captured["config"] = config
        yield "values", dict(state)

    graph._compiled_graph.astream = MagicMock(side_effect=_astream)

    with patch("core.langfuse_client.get_langfuse_handler", return_value=handler):
        await _drain_stream(graph, make_agent_state(room_id=9, message_id=99))

    cfg = captured["config"]
    assert cfg["callbacks"] == [handler]
    meta = cfg["metadata"]
    assert meta["langfuse_session_id"] == "9"
    assert meta["message_id"] == 99
