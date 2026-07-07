"""POST /chat/stream 라우터 테스트 공유 헬퍼/픽스처.

test_chat_router_stream.py / test_chat_router_integration.py 가 공유한다.
httpx.AsyncClient로 SSE 스트리밍을 검증한다.
AgentWorkflow는 AsyncMock으로 패치하여 LLM/DB 호출 없이 단위 테스트한다.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.helpers import make_agent_state
from schemas.state import AgentState, IntentType


@pytest.fixture(autouse=True)
def _mock_redis_io():
    """모든 chat router 테스트에서 Redis 해석을 mock으로 대체한다.

    AgentGraph(Answer Cache 용)가 사용하는 _resolve_redis 를 mock 으로 대체하여
    실제 Redis 연결 시도 없이 동작하도록 한다.
    """
    with patch("routers.chat._resolve_redis", return_value=MagicMock()):
        yield


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


_CHAT_TRACE = {"node_path": ["router", "sql_agent", "answer"], "elapsed_ms": 100}


def _make_final_state(**kwargs) -> AgentState:
    return make_agent_state(
        **{
            "intent": IntentType.SQL_SEARCH,
            "answer": "강남구 수영장 목록입니다.",
            "trace": _CHAT_TRACE,
            **kwargs,
        }
    )


def _parse_sse_events(content: bytes) -> list[dict]:
    """SSE 응답 바이트를 파싱하여 {event, data} 목록으로 반환한다."""
    events: list[dict] = []
    current: dict = {}
    for line in content.decode().splitlines():
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


def _make_stream(final_state: AgentState):
    """workflow.stream()을 모사하는 async generator factory를 반환한다.

    정상 흐름: progress 3개(routing → searching → answering) + result 1개.
    """

    async def _gen(*args, **kwargs):
        yield "progress", {"step": "routing", "message": "질문을 분석하고 있습니다..."}
        yield (
            "progress",
            {"step": "searching", "message": "관련 정보를 검색하고 있습니다..."},
        )
        yield (
            "progress",
            {"step": "answering", "message": "답변을 생성하고 있습니다..."},
        )
        yield "result", final_state

    return _gen


# ---------------------------------------------------------------------------
# 앱 픽스처 — main.py app을 import해 재사용
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    from main import app as _app

    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
