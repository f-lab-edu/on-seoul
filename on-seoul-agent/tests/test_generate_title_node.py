"""generate_title_node — 독립 병렬 제목 생성 노드 검증.

검증 대상:
  1. 첫 턴 캐시 미스: LLM 호출 → title 이벤트 emit(type/room_id/title/message_id/query) + 캐시 set.
  2. 캐시 히트: LLM 미호출로 emit.
  3. title_needed=False: no-op, emit 없음.
  4. fail-open: LLM 예외 → emit 없음, return {}.
  5. 빈/공백 title: emit 생략.
  6. 캐시 키 정규화(strip/공백 collapse/NFC) — 동일 정규화 메시지는 같은 키.
  7. graph.stream(): 첫 턴이면 title 이벤트가 SSE 튜플로 흐른다.
  8. final payload 회귀: title 키 없음.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.nodes.title import (
    _TITLE_PROMPT_VERSION,
    _normalize_message,
    _title_cache_key,
    TitleNodes,
)
from schemas.intake import IntakeAction, TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_ai_session,
    make_intake,
    make_router,
    make_sql_agent,
    stream_graph,
)


def _make_title_agent(title: str = "수영장 안내", raise_exc: Exception | None = None):
    """고정 title 을 반환하는 title chain mock 을 보유한 TitleNodes factory.

    title chain 은 _TitleOutput(title=...) 을 반환하는 ainvoke 를 가진다.
    """
    from agents.nodes.title import _TitleOutput

    chain = MagicMock()
    if raise_exc is not None:
        chain.ainvoke = AsyncMock(side_effect=raise_exc)
    else:
        chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=title))
    return chain


def _make_redis(get_return=None):
    redis = MagicMock()
    redis.get = AsyncMock(return_value=get_return)
    redis.set = AsyncMock()
    return redis


class _CapturingWriter:
    """get_stream_writer mock — writer 호출 페이로드를 수집한다."""

    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)


# ---------------------------------------------------------------------------
# 정규화 / 캐시 키
# ---------------------------------------------------------------------------


class TestNormalizeAndKey:
    def test_normalize_strip_and_collapse(self):
        assert _normalize_message("  수영장   알려줘  ") == "수영장 알려줘"

    def test_normalize_nfc(self):
        # 분해형 한글(NFD) → 결합형(NFC) 정규화로 같은 문자열이 된다.
        import unicodedata

        nfd = unicodedata.normalize("NFD", "수영장")
        assert _normalize_message(nfd) == "수영장"

    def test_key_includes_version(self):
        key = _title_cache_key("수영장 알려줘")
        assert key.startswith(f"title:{_TITLE_PROMPT_VERSION}:")

    def test_key_stable_across_whitespace(self):
        assert _title_cache_key("수영장  알려줘") == _title_cache_key(" 수영장 알려줘 ")


# ---------------------------------------------------------------------------
# 노드 단위 동작
# ---------------------------------------------------------------------------


class TestGenerateTitleNode:
    async def _run(self, node, state):
        writer = _CapturingWriter()
        with patch("agents.nodes.title.get_stream_writer", return_value=writer):
            result = await node.generate_title_node(state)
        return result, writer

    async def test_cache_miss_emits_and_sets(self):
        """캐시 미스 → LLM 호출 + title 이벤트 emit + 캐시 set."""
        chain = _make_title_agent("수영장 안내")
        redis = _make_redis(get_return=None)
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(
            room_id=7, message_id=1, message="수영장 알려줘", title_needed=True
        )

        result, writer = await self._run(node, state)

        chain.ainvoke.assert_awaited_once()
        assert result == {}, "공유 state 에 쓰지 않는다(fire-and-emit only)"
        assert len(writer.payloads) == 1
        payload = writer.payloads[0]
        assert payload["_evt"] == "title"
        assert payload["type"] == "title"
        assert payload["room_id"] == 7
        assert payload["message_id"] == 1
        assert payload["title"] == "수영장 안내"
        assert payload["query"] == "수영장 알려줘"
        redis.set.assert_awaited_once()

    async def test_cache_hit_no_llm(self):
        """캐시 히트 → LLM 미호출, 캐시된 title emit."""
        chain = _make_title_agent("무시될 제목")
        redis = _make_redis(get_return="캐시 제목")
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(message_id=1, message="수영장 알려줘", title_needed=True)

        result, writer = await self._run(node, state)

        chain.ainvoke.assert_not_awaited()
        assert result == {}
        assert len(writer.payloads) == 1
        assert writer.payloads[0]["title"] == "캐시 제목"
        redis.set.assert_not_awaited()

    async def test_title_not_needed_noop(self):
        """title_needed=False → no-op, emit/캐시 접근 없음."""
        chain = _make_title_agent()
        redis = _make_redis()
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(message_id=2, title_needed=False)

        result, writer = await self._run(node, state)

        assert result == {}
        assert writer.payloads == []
        chain.ainvoke.assert_not_awaited()
        redis.get.assert_not_awaited()

    async def test_llm_exception_fail_open(self):
        """LLM 예외 → emit 없음, return {} (스트림 중단 금지)."""
        chain = _make_title_agent(raise_exc=RuntimeError("LLM down"))
        redis = _make_redis(get_return=None)
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(message_id=1, title_needed=True)

        result, writer = await self._run(node, state)

        assert result == {}
        assert writer.payloads == []

    async def test_cache_exception_fail_open(self):
        """캐시 조회 예외 → emit 없음, return {}."""
        chain = _make_title_agent("제목")
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=RuntimeError("redis down"))
        redis.set = AsyncMock()
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(message_id=1, title_needed=True)

        result, writer = await self._run(node, state)

        assert result == {}
        assert writer.payloads == []

    async def test_blank_title_no_emit(self):
        """LLM 이 빈/공백 title 반환 → emit 생략, 캐시 set 안 함."""
        chain = _make_title_agent("   ")
        redis = _make_redis(get_return=None)
        node = TitleNodes(title_chain=chain, redis=redis)
        state = make_agent_state(message_id=1, title_needed=True)

        result, writer = await self._run(node, state)

        assert result == {}
        assert writer.payloads == []
        redis.set.assert_not_awaited()


# ---------------------------------------------------------------------------
# 그래프 배선 / SSE
# ---------------------------------------------------------------------------


class TestStreamTitleEvent:
    async def _collect(self, graph, state, **kwargs):
        events = []
        async for ev in stream_graph(graph, state, **kwargs):
            events.append(ev)
        return events

    async def test_title_event_emitted_first_turn(self):
        """첫 턴(title_needed=True) → graph.stream 이 title 이벤트를 yield 한다."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="수영장 검색입니다.",
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        redis = _make_redis(get_return=None)
        graph = AgentGraph(
            intake=intake,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
            redis=redis,
        )
        # title chain 을 fake 로 교체
        graph._nodes._title._title_chain = _make_title_agent("수영장 안내")

        events = await self._collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        title_events = [(t, d) for t, d in events if t == "title"]
        assert len(title_events) == 1
        _, data = title_events[0]
        assert data["type"] == "title"
        assert data["title"] == "수영장 안내"
        assert data["query"] == "수영장 알려줘"

    async def test_no_title_event_when_not_needed(self):
        """후속 턴(title_needed=False) → title 이벤트 없음."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="수영장 검색입니다.",
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        redis = _make_redis(get_return=None)
        graph = AgentGraph(
            intake=intake,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
            redis=redis,
        )

        events = await self._collect(
            graph,
            make_agent_state(message_id=2, message="더 알려줘", title_needed=False),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        title_events = [(t, d) for t, d in events if t == "title"]
        assert title_events == []

    async def test_final_payload_has_no_title(self):
        """회귀: final result payload 에 title 키가 없다."""
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale="수영장 검색입니다.",
        )
        router = make_router(IntentType.SQL_SEARCH)
        sql_agent, data_session = make_sql_agent([])
        redis = _make_redis(get_return=None)
        graph = AgentGraph(
            intake=intake,
            router=router,
            sql_agent=sql_agent,
            answer_agent=make_answer_agent(),
            redis=redis,
        )
        graph._nodes._title._title_chain = _make_title_agent("수영장 안내")

        events = await self._collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert len(result_events) == 1
        _, result = result_events[0]
        assert "title" not in (result.get("output") or {})
