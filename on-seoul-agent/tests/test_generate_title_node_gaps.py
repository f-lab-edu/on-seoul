"""generate_title_node — QA 커버리지 갭 보강(독립 검증).

기존 test_generate_title_node.py 와 중복하지 않고 다음 갭만 채운다.

1. SSE 튜플 페이로드 필드 정확성: graph.stream 이 yield 하는 ("title", data) 가
   정확히 5개 키(type/room_id/title/message_id/query)만 가지고 _evt 가 제거되는지.
2. 순서 독립 통합: title 이벤트가 progress/decision/result 와 같은 스트림에 함께
   흘러도(순서 무관) 모두 정상 산출되는지.
3. title_needed=False 그래프 경로에서 캐시(redis)조차 건드리지 않는지(no-op).
4. 캐시 키 정규화 경계: 서로 다른 메시지는 다른 키, NFC 차이만 있는 메시지는 같은 키이며
   _resolve_title 가 NFC 정규화 키로 캐시 히트하는지.
5. fail-open 후 스트림이 정상 완료(result 도달)하는지 — emit 누락이 그래프를 막지 않음.
"""

import unicodedata
from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.nodes.title import _title_cache_key, _TitleOutput, TitleNodes
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


def _title_chain(title: str = "수영장 안내"):
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_TitleOutput(title=title))
    return chain


def _redis(get_return=None):
    redis = MagicMock()
    redis.get = AsyncMock(return_value=get_return)
    redis.set = AsyncMock()
    return redis


def _graph(redis, *, intent=IntentType.SQL_SEARCH, sql_rows=None):
    intake = make_intake(
        turn_kind=TurnKind.NEW,
        action=IntakeAction.RETRIEVE,
        user_rationale="수영장 검색입니다.",
    )
    router = make_router(intent)
    sql_agent, data_session = make_sql_agent(sql_rows or [])
    graph = AgentGraph(
        intake=intake,
        router=router,
        sql_agent=sql_agent,
        answer_agent=make_answer_agent(),
        redis=redis,
    )
    return graph, data_session


async def _collect(graph, state, **kwargs):
    return [ev async for ev in stream_graph(graph, state, **kwargs)]


# ---------------------------------------------------------------------------
# 1. SSE 튜플 페이로드 필드 정확성 (그래프 매핑 단)
# ---------------------------------------------------------------------------


class TestTitleTuplePayload:
    async def test_title_tuple_has_exactly_five_keys_no_evt(self):
        """("title", data) 의 data 는 정확히 5개 키만 갖고 _evt 는 벗겨진다.

        Spring 릴레이가 event name 을 벗겨도 payload type:"title" 로 식별 가능해야 하므로
        type 키 존재가 필수다. 내부 라우팅 키 _evt 는 외부로 새면 안 된다.
        """
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        graph._nodes._title._title_chain = _title_chain("수영장 안내")

        events = await _collect(
            graph,
            make_agent_state(
                room_id=9, message_id=1, message="수영장 알려줘", title_needed=True
            ),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        title_data = [d for t, d in events if t == "title"]
        assert len(title_data) == 1
        data = title_data[0]
        assert set(data.keys()) == {"type", "room_id", "title", "message_id", "query"}
        assert "_evt" not in data
        assert data["type"] == "title"
        assert data["room_id"] == 9
        assert data["message_id"] == 1
        assert data["title"] == "수영장 안내"
        assert data["query"] == "수영장 알려줘"


# ---------------------------------------------------------------------------
# 2. 순서 독립 통합 — title 이 다른 이벤트와 같은 스트림에 섞여 나온다
# ---------------------------------------------------------------------------


class TestTitleStreamInterleaving:
    async def test_title_coexists_with_progress_and_result(self):
        """title 이벤트가 progress/result 와 같은 스트림에 함께 산출된다(순서 무관).

        title 의 존재가 다른 이벤트를 누락시키지 않고, 다른 이벤트도 title 을
        누락시키지 않음을 보장한다(병렬 분기 독립성).
        """
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        graph._nodes._title._title_chain = _title_chain("수영장 안내")

        events = await _collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        types = [t for t, _ in events]
        assert "title" in types, "title 이벤트 누락"
        assert "result" in types, "result 누락 — title 이 그래프 완료를 막았다"
        # title 한 번, result 한 번(정상 완료).
        assert types.count("title") == 1
        assert types.count("result") == 1
        # progress 류도 정상적으로 함께 흐른다(병렬 분기가 본류를 막지 않음).
        assert "progress" in types

    async def test_title_event_position_is_not_asserted(self):
        """title 이벤트는 다른 이벤트 대비 어느 위치에 와도 무방하다(순서 비결정 허용).

        병렬 분기라 super-step 스케줄에 따라 title 이 progress 앞/뒤 어디든 올 수 있다.
        이 테스트는 '위치에 의존하지 않는다'는 계약을 명문화한다 — 존재만 검증.
        """
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        graph._nodes._title._title_chain = _title_chain("수영장 안내")

        events = await _collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )
        title_idxs = [i for i, (t, _) in enumerate(events) if t == "title"]
        result_idxs = [i for i, (t, _) in enumerate(events) if t == "result"]
        # 둘 다 정확히 1회 산출되면 위치는 자유다.
        assert len(title_idxs) == 1 and len(result_idxs) == 1


# ---------------------------------------------------------------------------
# 3. title_needed=False → 그래프 경로에서 캐시 미접근
# ---------------------------------------------------------------------------


class TestTitleNotNeededGraph:
    async def test_no_cache_access_when_not_needed_in_graph(self):
        """후속 턴(title_needed=False) → generate_title_node 가 redis 를 건드리지 않는다.

        노드 단위 테스트(test_title_not_needed_noop)와 별개로, 실제 그래프 실행에서도
        게이트가 캐시 조회 이전에 걸리는지 확인한다.
        """
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        # title 노드 전용 redis 스파이로 교체 — 그래프 공용 redis(캐시/보정 노드 등이
        # 함께 사용)와 분리해 title 게이트 효과만 정밀 관측한다.
        title_redis = _redis(get_return=None)
        graph._nodes._title._redis = title_redis
        spy_chain = _title_chain("무시")
        graph._nodes._title._title_chain = spy_chain

        events = await _collect(
            graph,
            make_agent_state(message_id=2, message="더 알려줘", title_needed=False),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        assert [t for t, _ in events if t == "title"] == []
        title_redis.get.assert_not_awaited()
        title_redis.set.assert_not_awaited()
        spy_chain.ainvoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. 캐시 키 정규화 경계
# ---------------------------------------------------------------------------


class TestCacheKeyBoundaries:
    def test_distinct_messages_distinct_keys(self):
        """서로 다른 의미의 메시지는 다른 캐시 키를 갖는다(충돌 회피)."""
        assert _title_cache_key("수영장 알려줘") != _title_cache_key("도서관 알려줘")

    def test_nfc_equivalent_messages_same_key(self):
        """NFC 차이만 있는 메시지는 같은 키 — 분해형/결합형 한글이 같은 엔트리로 매핑."""
        nfd = unicodedata.normalize("NFD", "수영장 알려줘")
        nfc = unicodedata.normalize("NFC", "수영장 알려줘")
        assert nfd != nfc  # 바이트 표현은 다르지만
        assert _title_cache_key(nfd) == _title_cache_key(nfc)  # 키는 같다

    def test_internal_tab_newline_collapse(self):
        """탭/개행 등 공백류도 단일 스페이스로 collapse 되어 동일 키가 된다."""
        assert _title_cache_key("수영장\t알려줘") == _title_cache_key("수영장 알려줘")
        assert _title_cache_key("수영장\n알려줘") == _title_cache_key("수영장 알려줘")

    async def test_resolve_title_cache_hit_via_normalized_key(self):
        """_resolve_title 가 NFC 정규화 키로 캐시 히트한다 — 분해형 입력도 같은 엔트리 사용.

        분해형(NFD) 메시지로 들어와도 정규화 키로 조회하므로, 결합형(NFC)으로 set 된
        캐시를 동일하게 히트해야 한다(LLM 미호출).
        """
        nfc_key = _title_cache_key(unicodedata.normalize("NFC", "수영장 알려줘"))

        store = {nfc_key: "캐시된 제목"}
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=lambda k: store.get(k))
        redis.set = AsyncMock()
        chain = _title_chain("LLM 제목")
        node = TitleNodes(title_chain=chain, redis=redis)

        nfd_message = unicodedata.normalize("NFD", "수영장 알려줘")
        title = await node._resolve_title(nfd_message)

        assert title == "캐시된 제목"
        chain.ainvoke.assert_not_awaited()


# ---------------------------------------------------------------------------
# 5. fail-open 후 스트림 정상 완료
# ---------------------------------------------------------------------------


class TestEmitOutsideStreamContext:
    """_emit_title 의 stream 컨텍스트 부재 처리(에러 경로) — 그래프 밖 직접 호출 가정.

    generate_title_node 가 stream() 밖(예: 단순 ainvoke 경로)에서 실행되면
    get_stream_writer() 가 RuntimeError/LookupError 를 던지거나 None 을 줄 수 있다.
    이때 노드는 조용히 no-op 해야 하며(스트림 중단/예외 전파 금지), 항상 {} 를 반환한다.
    """

    async def test_writer_lookup_error_is_noop(self):
        """get_stream_writer 가 LookupError → emit 생략, 예외 미전파, return {}."""
        chain = _title_chain("수영장 안내")
        node = TitleNodes(title_chain=chain, redis=_redis(get_return=None))
        state = make_agent_state(
            message_id=1, message="수영장 알려줘", title_needed=True
        )

        with patch(
            "agents.nodes.title.get_stream_writer",
            side_effect=LookupError("no stream context"),
        ):
            result = await node.generate_title_node(state)

        assert result == {}
        # 캐시 set 은 emit 이전 단계라 정상 수행됐다(생성 자체는 성공).
        chain.ainvoke.assert_awaited_once()

    async def test_writer_runtime_error_is_noop(self):
        """get_stream_writer 가 RuntimeError → emit 생략, 예외 미전파, return {}."""
        chain = _title_chain("수영장 안내")
        node = TitleNodes(title_chain=chain, redis=_redis(get_return=None))
        state = make_agent_state(
            message_id=1, message="수영장 알려줘", title_needed=True
        )

        with patch(
            "agents.nodes.title.get_stream_writer",
            side_effect=RuntimeError("not in a runnable context"),
        ):
            result = await node.generate_title_node(state)

        assert result == {}

    async def test_writer_none_is_noop(self):
        """get_stream_writer 가 None → emit 생략, return {} (writer 호출 안 함)."""
        chain = _title_chain("수영장 안내")
        node = TitleNodes(title_chain=chain, redis=_redis(get_return=None))
        state = make_agent_state(
            message_id=1, message="수영장 알려줘", title_needed=True
        )

        with patch("agents.nodes.title.get_stream_writer", return_value=None):
            result = await node.generate_title_node(state)

        assert result == {}


class TestFailOpenStreamCompletes:
    async def test_llm_failure_does_not_block_stream(self):
        """title LLM 예외(fail-open) 시에도 그래프는 result 까지 정상 완료한다.

        title 이벤트는 누락되지만 본류 스트림(result)은 영향받지 않아야 한다.
        """
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        failing = MagicMock()
        failing.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
        graph._nodes._title._title_chain = failing

        events = await _collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        types = [t for t, _ in events]
        assert "title" not in types, "fail-open 시 title emit 생략"
        assert types.count("result") == 1, "fail-open 이 그래프 완료를 막지 않아야 한다"

    async def test_blank_title_does_not_block_stream(self):
        """빈/공백 title 도 emit 생략 + 스트림 정상 완료."""
        redis = _redis(get_return=None)
        graph, data_session = _graph(redis)
        # title 노드 전용 redis 스파이 — 그래프 공용 redis 와 분리해 title 캐시 set
        # 미발생만 정밀 관측한다(공용 redis 는 다른 노드가 사용).
        title_redis = _redis(get_return=None)
        graph._nodes._title._redis = title_redis
        graph._nodes._title._title_chain = _title_chain("   ")

        events = await _collect(
            graph,
            make_agent_state(message="수영장 알려줘", title_needed=True),
            data_session=data_session,
            ai_session=make_ai_session(),
        )

        types = [t for t, _ in events]
        assert "title" not in types
        assert types.count("result") == 1
        # 빈 title 은 캐시 set 도 하지 않는다.
        title_redis.set.assert_not_awaited()
