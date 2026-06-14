"""대화 맥락 유지(history) 구조 회귀 테스트.

AgentState 필드 / Redis 의존 제거 / router_node 주입을 검증한다.
"""

from unittest.mock import MagicMock

from agents.nodes import GraphNodes
from schemas.chat import ChatRequest, HistoryTurn
from schemas.state import AgentState
from tests.helpers import (
    make_agent_state,
    make_analytics_agent,
    make_answer_agent,
    make_router,
    make_sql_agent,
)
from schemas.state import IntentType


def test_agent_state_has_history_not_recent_queries():
    """AgentState 어노테이션에 history가 있고 recent_queries가 없다."""
    annotations = AgentState.__annotations__
    assert "history" in annotations
    assert "recent_queries" not in annotations


def test_chat_request_history_default_empty():
    """ChatRequest.history 미전송 시 기본값 빈 배열."""
    req = ChatRequest(room_id=1, message_id=1, message="강남구 수영장")
    assert req.history == []


def test_chat_request_history_model_dump_shape():
    """HistoryTurn은 {role, content} dict로 model_dump 된다."""
    req = ChatRequest(
        room_id=1,
        message_id=2,
        message="그 중 무료",
        history=[HistoryTurn(role="user", content="강남구 수영장")],
    )
    assert [h.model_dump() for h in req.history] == [
        {"role": "user", "content": "강남구 수영장"}
    ]


def test_prev_intent_known_value_parsed():
    """알려진 prev_intent 문자열은 그대로 IntentType 으로 파싱된다."""
    req = ChatRequest(
        room_id=1, message_id=1, message="질문", prev_intent="VECTOR_SEARCH"
    )
    assert req.prev_intent == IntentType.VECTOR_SEARCH


def test_prev_intent_unknown_value_falls_back_to_none():
    """SHOULD-FIX 2: 알 수 없는/오타 prev_intent 는 422 대신 None 으로 폴백한다.

    Spring 이 미래의 신규 intent 나 오타를 회신해도 요청 전체가 실패하지 않는다.
    """
    req = ChatRequest(
        room_id=1, message_id=1, message="질문", prev_intent="FUTURE_INTENT"
    )
    assert req.prev_intent is None

    req2 = ChatRequest(room_id=1, message_id=1, message="질문", prev_intent="sql_search")
    assert req2.prev_intent is None  # 대소문자 불일치도 unknown 취급


def test_no_recent_queries_module_import_in_production_code():
    """프로덕션 코드 어디에도 core.recent_queries import가 없다."""
    import importlib

    for module_name in ("routers.chat", "agents.nodes", "agents.router_agent"):
        source = importlib.import_module(module_name).__file__
        with open(source, encoding="utf-8") as fh:
            text = fh.read()
        assert "core.recent_queries" not in text, module_name
        assert "get_recent_queries" not in text, module_name
        assert "push_recent_query" not in text, module_name


async def test_router_node_passes_history_to_classify():
    """router_node가 state["history"]를 RouterAgent.classify(history=...)로 전달한다."""
    router = make_router(IntentType.SQL_SEARCH)
    nodes = GraphNodes(
        router=router,
        sql_agent=make_sql_agent([])[0],
        vector_agent=MagicMock(),
        answer_agent=make_answer_agent(),
        analytics_agent=make_analytics_agent([])[0],
        redis=None,
    )
    # classify 호출 인자를 캡처
    captured: dict = {}
    real_structured = router._llm.with_structured_output.return_value

    async def _capturing_classify(message, history=None):
        captured["message"] = message
        captured["history"] = history
        return await real_structured.ainvoke([])

    router.classify = _capturing_classify

    history = [
        {"role": "user", "content": "강남구 수영장"},
        {"role": "assistant", "content": "3건입니다."},
    ]
    state = make_agent_state(
        message_id=2,
        message="그 중 무료인 것만",
        history=history,
    )

    await nodes.router_node(state)

    assert captured["message"] == "그 중 무료인 것만"
    assert captured["history"] == history
