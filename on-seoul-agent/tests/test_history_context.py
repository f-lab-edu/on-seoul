"""대화 맥락 유지(history) 구조 회귀 테스트.

§5-2 항목 중 AgentState 필드 / Redis 의존 제거 / router_node 주입을 검증한다.
"""

from unittest.mock import MagicMock

from agents.nodes import GraphNodes
from schemas.chat import ChatRequest, HistoryTurn
from schemas.state import AgentState
from tests.helpers import (
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
    nodes.data_session = MagicMock()
    nodes.ai_session = MagicMock()
    nodes.node_path = []

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
    state = AgentState(
        room_id=1,
        message_id=2,
        message="그 중 무료인 것만",
        title_needed=False,
        intent=None,
        user_lat=None,
        user_lng=None,
        refined_query=None,
        max_class_name=None,
        area_name=None,
        service_status=None,
        sql_results=None,
        sql_keyword=None,
        vector_sub_intent=None,
        vector_results=None,
        map_results=None,
        analytics_results=None,
        analytics_group_by=None,
        analytics_metric=None,
        analytics_keyword=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
        retry_count=0,
        history=history,
        cache_hit=False,
        search_channels={},
        hydrated_services=None,
        service_cards=None,
    )

    await nodes.router_node(state)

    assert captured["message"] == "그 중 무료인 것만"
    assert captured["history"] == history
