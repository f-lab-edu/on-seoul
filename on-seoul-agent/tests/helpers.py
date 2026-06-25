"""테스트 공통 헬퍼 — AgentState 팩토리 및 그래프 mock 빌더.

AgentState에 필드가 추가될 때 make_agent_state만 수정하면 된다.
각 테스트 파일은 이 함수를 호출하는 얇은 래퍼로 파일별 기본값만 선언한다.

사용법::

    from tests.helpers import make_agent_state, make_router, make_sql_agent
    state = make_agent_state(intent=IntentType.SQL_SEARCH, message="테스트")
    router = make_router(IntentType.SQL_SEARCH)
"""

from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from agents.analytics_agent import AnalyticsAgent, _AnalyticsParams
from agents.answer_agent import (
    AnswerAgent,
    _TitleOutput,
    _compose,
    _FALLBACK_GUARDRAILS,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_ANALYTICS,
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_CLARIFY,
    _STRUCT_DESCRIBE,
    _STRUCT_DESCRIBE_EMPTY,
    _STRUCT_DETAIL,
    _STRUCT_EXPLAIN,
    _STRUCT_FALLBACK,
    _STRUCT_MAP,
)
from agents.intake_agent import IntakeAgent
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent, _SqlParams
from agents.triage_agent import TriageAgent, TriageOutput
from schemas.intake import IntakeAction, IntakeOutput, TurnKind
from schemas.state import ActionType, AgentState, IntentType


# 평면 도메인 키 → 중첩 채널/leaf 매핑.
# AgentState 도메인 중첩 리팩터 후에도 기존 테스트가 평면 kwargs 로 상태를 조립할 수
# 있도록, make_agent_state 가 아래 매핑으로 평면 override 를 중첩 채널에 분배한다.
# 중첩 override(triage={...}/plan={...} 등)도 그대로 받아 머지한다.
_FLAT_TO_NESTED: dict[str, tuple[str, str]] = {
    # plan
    "intent": ("plan", "intent"),
    "refined_query": ("plan", "refined_query"),
    "vector_sub_intent": ("plan", "vector_sub_intent"),
    "secondary_intent": ("plan", "secondary_intent"),
    # filters
    "max_class_name": ("filters", "max_class_name"),
    "area_name": ("filters", "area_name"),
    "service_status": ("filters", "service_status"),
    "payment_type": ("filters", "payment_type"),
    # triage
    "action": ("triage", "action"),
    "out_of_scope_type": ("triage", "out_of_scope_type"),
    "user_rationale": ("triage", "user_rationale"),
    # sql
    "sql_results": ("sql", "results"),
    "sql_keyword": ("sql", "keyword"),
    # vector
    "vector_results": ("vector", "results"),
    # map
    "map_results": ("map", "results"),
    # analytics
    "analytics_results": ("analytics", "results"),
    "analytics_group_by": ("analytics", "group_by"),
    "analytics_metric": ("analytics", "metric"),
    "analytics_keyword": ("analytics", "keyword"),
    # hydration
    "hydrated_services": ("hydration", "hydrated_services"),
    # output
    "answer": ("output", "answer"),
    "title": ("output", "title"),
    "service_cards": ("output", "service_cards"),
    # emit
    "decision_emitted": ("emit", "decision_emitted"),
    "searching_emitted": ("emit", "searching_emitted"),
    "answering_emitted": ("emit", "answering_emitted"),
}

_NESTED_CHANNELS: frozenset[str] = frozenset(
    {
        "triage",
        "plan",
        "filters",
        "sql",
        "vector",
        "map",
        "analytics",
        "hydration",
        "output",
        "emit",
    }
)


def make_agent_state(**overrides: Any) -> AgentState:
    """AgentState 테스트 팩토리 — 최소 유효 상태를 기본값으로 반환한다.

    평면 도메인 kwargs(intent/sql_results/...)는 _FLAT_TO_NESTED 매핑으로 중첩
    채널에 분배된다. 중첩 채널 override(plan={...} 등)도 그대로 받아 머지한다.
    """
    base = AgentState(
        # ── 보편/carryover/재시도/오류/인프라 (평면) ──
        room_id=1,
        message_id=1,
        message="수영장 알려줘",
        title_needed=False,
        user_lat=None,
        user_lng=None,
        history=[],
        prev_entities=None,
        prev_intent=None,
        prev_reasoning=None,
        prev_working_set=None,
        target_service_ids=None,
        result_quality=None,
        reservation_guide_shown=False,
        retry_count=0,
        retry_relaxed=False,
        relaxed_filters=None,
        forced_intent=None,
        retry_radius_m=None,
        error=None,
        cache_hit=False,
        answer_lock_key=None,
        node_path=[],
        search_channels={},
        trace=None,
        started_at=None,
        rrf_merged_ids=None,
        # ── 도메인 working state (중첩) ──
        triage={},
        plan={},
        filters={},
        sql={},
        vector={},
        map={},
        analytics={},
        hydration={},
        output={},
        emit={},
    )
    for key, value in overrides.items():
        nested = _FLAT_TO_NESTED.get(key)
        if nested is not None:
            channel, leaf = nested
            base[channel][leaf] = value  # type: ignore[literal-required]
        elif key in _NESTED_CHANNELS and isinstance(value, dict):
            # 중첩 채널 override(plan={...} 등) — 머지.
            base[key].update(value)  # type: ignore[literal-required]
        else:
            base[key] = value  # type: ignore[literal-required]
    return base


# ---------------------------------------------------------------------------
# 그래프 단위 테스트용 mock 빌더 (test_graph.py, test_graph_search_persist.py 공용)
# ---------------------------------------------------------------------------


def make_router(
    intent: IntentType,
    *,
    refined_query: str | None = None,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    vector_sub_intent: str | None = None,
    secondary_intent: IntentType | None = None,
) -> RouterAgent:
    """주어진 intent + 검색 계획(refined_query/post-filter/secondary)을 반환하는 RouterAgent mock."""
    agent = RouterAgent.__new__(RouterAgent)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(
        return_value=_IntentOutput(
            intent=intent,
            refined_query=refined_query,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
            vector_sub_intent=vector_sub_intent,  # type: ignore[arg-type]
            secondary_intent=secondary_intent,
        )
    )
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    return agent


def make_triage(
    action: ActionType,
    intent: IntentType | None = None,
    *,
    refined_query: str | None = None,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    vector_sub_intent: str | None = None,
    secondary_intent: IntentType | None = None,
    out_of_scope_type: str | None = None,
    user_rationale: str | None = None,
) -> TriageAgent:
    """주어진 action 을 항상 반환하는 TriageAgent mock (action 결정 전담).

    검색 계획 인자(intent/refined_query/post-filter/secondary_intent)는 더 이상
    TriageOutput 에 들어가지 않는다(RouterAgent 책임). 그래프 E2E 테스트는
    `make_triage(...)` 와 `make_triage_router(...)` 를 짝지어 사용하거나,
    AgentGraph 에 `router=make_router(...)` 를 함께 주입한다.

    하위호환: 본 헬퍼는 검색 계획 키워드 인자를 받아도 무시한다(시그니처 호환용).
    """
    del intent, refined_query, max_class_name, area_name, service_status
    del payment_type, vector_sub_intent, secondary_intent
    agent = TriageAgent.__new__(TriageAgent)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(
        return_value=TriageOutput(
            action=action,
            out_of_scope_type=out_of_scope_type,  # type: ignore[arg-type]
            user_rationale=user_rationale,
        )
    )
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    agent._build_context_block = lambda history: ""
    return agent


def make_intake(
    *,
    turn_kind: TurnKind = TurnKind.NEW,
    action: IntakeAction = IntakeAction.RETRIEVE,
    oos_type: str | None = None,
    ref_indices: list[int] | None = None,
    user_rationale: str | None = None,
    raise_exc: Exception | None = None,
) -> IntakeAgent:
    """고정 IntakeOutput 을 반환하는 IntakeAgent mock (입구 단일화 fake LLM).

    raise_exc 가 주어지면 classify 가 그 예외를 던진다((B) 노드 예외 폴백 검증용).
    """
    agent = IntakeAgent.__new__(IntakeAgent)
    structured = MagicMock()
    if raise_exc is not None:
        structured.ainvoke = AsyncMock(side_effect=raise_exc)
    else:
        structured.ainvoke = AsyncMock(
            return_value=IntakeOutput(
                turn_kind=turn_kind,
                action=action,
                oos_type=oos_type,  # type: ignore[arg-type]
                ref_indices=ref_indices or [],
                user_rationale=user_rationale,
            )
        )
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    return agent


def make_intake_router(
    *,
    turn_kind: TurnKind = TurnKind.NEW,
    action: IntakeAction = IntakeAction.RETRIEVE,
    oos_type: str | None = None,
    ref_indices: list[int] | None = None,
    user_rationale: str | None = None,
    intent: IntentType | None = None,
    refined_query: str | None = None,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    vector_sub_intent: str | None = None,
    secondary_intent: IntentType | None = None,
) -> tuple[IntakeAgent, RouterAgent]:
    """(intake, router) 한 쌍 — NEW+RETRIEVE E2E 테스트용.

    intake 는 turn_kind/action 을, router 는 intent + 검색 계획을 산출한다.
    """
    intake = make_intake(
        turn_kind=turn_kind,
        action=action,
        oos_type=oos_type,
        ref_indices=ref_indices,
        user_rationale=user_rationale,
    )
    router = make_router(
        intent if intent is not None else IntentType.FALLBACK,
        refined_query=refined_query,
        max_class_name=max_class_name,
        area_name=area_name,
        service_status=service_status,
        payment_type=payment_type,
        vector_sub_intent=vector_sub_intent,
        secondary_intent=secondary_intent,
    )
    return intake, router


def make_triage_router(
    action: ActionType,
    intent: IntentType | None = None,
    *,
    refined_query: str | None = None,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    vector_sub_intent: str | None = None,
    secondary_intent: IntentType | None = None,
    out_of_scope_type: str | None = None,
    user_rationale: str | None = None,
) -> tuple[TriageAgent, RouterAgent]:
    """(triage, router) 한 쌍을 생성한다 — RETRIEVE E2E 테스트용.

    triage 는 action 을, router 는 intent + 검색 계획을 산출한다.
    RETRIEVE 경로에서 intent 가 router_node 로부터 흐르도록 짝지어 준다.
    """
    triage = make_triage(
        action,
        out_of_scope_type=out_of_scope_type,
        user_rationale=user_rationale,
    )
    router = make_router(
        intent if intent is not None else IntentType.FALLBACK,
        refined_query=refined_query,
        max_class_name=max_class_name,
        area_name=area_name,
        service_status=service_status,
        payment_type=payment_type,
        vector_sub_intent=vector_sub_intent,
        secondary_intent=secondary_intent,
    )
    return triage, router


def make_sql_agent(
    rows: list[dict],
    keyword: str | None = None,
) -> tuple[SqlAgent, MagicMock]:
    """rows 를 반환하는 SqlAgent mock + data_session mock."""
    agent = SqlAgent.__new__(SqlAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(return_value=_SqlParams(keyword=keyword))
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def make_analytics_agent(
    rows: list[dict],
    *,
    group_by: str = "max_class_name",
    metric: str = "count",
    keyword: str | None = None,
) -> tuple[AnalyticsAgent, MagicMock]:
    """rows 를 반환하는 AnalyticsAgent mock + data_session mock.

    _chain 은 주어진 group_by/metric/keyword 로 _AnalyticsParams 를 반환하고,
    data_session.execute 는 rows 를 group_value/count 형태로 돌려준다.
    """
    agent = AnalyticsAgent.__new__(AnalyticsAgent)
    chain = MagicMock()
    chain.ainvoke = AsyncMock(
        return_value=_AnalyticsParams(
            group_by=group_by,  # type: ignore[arg-type]
            metric=metric,  # type: ignore[arg-type]
            keyword=keyword,
        )
    )
    agent._chain = chain

    mock_result = MagicMock()
    mock_result.keys.return_value = list(rows[0].keys()) if rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    session = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    return agent, session


def make_answer_agent(
    answer: str = "답변입니다.",
    title: str | None = None,
) -> AnswerAgent:
    """고정 answer/title 을 반환하는 AnswerAgent mock."""
    agent = AnswerAgent.__new__(AnswerAgent)

    answer_chain = MagicMock()
    answer_chain.ainvoke = AsyncMock(return_value=answer)
    agent._answer_chain = answer_chain

    title_chain = MagicMock()
    title_chain.ainvoke = AsyncMock(
        return_value=_TitleOutput(title=title or "수영장 안내")
    )
    agent._title_chain = title_chain

    # Tier 1 정적 프롬프트 캐시 — 실제 __init__과 동일한 값으로 초기화.
    agent._static_prompts = {
        IntentType.MAP.value: _compose(_ROLE, _STRUCT_MAP, _OUTPUT_RULES),
        IntentType.ANALYTICS.value: _compose(_ROLE, _STRUCT_ANALYTICS, _OUTPUT_RULES),
        # FALLBACK 은 가드레일 블록을 추가로 끼워 조립한다(실제 __init__과 동기화).
        IntentType.FALLBACK.value: _compose(
            _ROLE, _STRUCT_FALLBACK, _FALLBACK_GUARDRAILS, _OUTPUT_RULES
        ),
        "DETAIL": _compose(_ROLE, _STRUCT_DETAIL, _OUTPUT_RULES),
        "ATTRIBUTE_GAP": _compose(_ROLE, _STRUCT_ATTRIBUTE_GAP, _OUTPUT_RULES),
        "DESCRIBE": _compose(_ROLE, _STRUCT_DESCRIBE, _OUTPUT_RULES),
        "DESCRIBE_EMPTY": _compose(_ROLE, _STRUCT_DESCRIBE_EMPTY, _OUTPUT_RULES),
        "CLARIFY": _compose(_ROLE, _STRUCT_CLARIFY, _FALLBACK_GUARDRAILS),
        "EXPLAIN": _compose(_ROLE, _STRUCT_EXPLAIN, _FALLBACK_GUARDRAILS),
    }
    return agent


def make_ai_session() -> MagicMock:
    """on_ai DB 세션 mock — execute/commit/rollback/begin_nested 지원."""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    # begin_nested()는 async context manager 로 사용된다.
    # MagicMock은 __aenter__/__aexit__ 를 AsyncMock 으로 자동 설정하므로
    # 별도 설정 없이 `async with session.begin_nested():` 가 동작한다.
    return session


def _ctx_factory(*sessions: Any):
    """호출마다 sessions 를 순서대로 yield 하는 asynccontextmanager 팩토리.

    노드 로컬 세션(0-6) 전환 후 노드는 `data_session_ctx()`/`ai_session_ctx()` 를
    직접 호출해 세션을 acquire-use-release 한다. 테스트는 이 팩토리로 두 ctx 를
    패치해, 노드가 어떤 mock 세션을 잡는지 제어/관측한다.

    sessions 가 1개면 매 호출 동일 세션을, 여러 개면 호출 순서대로 소비한다(여러
    번 세션을 여는 경로 — 예: retry 재진입 — 검증용). 다 소진하면 마지막 세션을
    반복 반환한다.
    """
    used: list[Any] = []
    seq = list(sessions)

    @asynccontextmanager
    async def _ctx():
        if seq:
            session = seq.pop(0) if len(seq) > 1 else seq[0]
        else:
            session = MagicMock()
        used.append(session)
        yield session

    _ctx.used = used  # type: ignore[attr-defined]
    return _ctx


async def run_graph(graph, state, *, data_session=None, ai_session=None):
    """graph.run(state) 를 노드 로컬 세션 ctx 패치와 함께 실행한다(테스트 전용).

    0-6 전환으로 graph.run() 은 세션 인자를 받지 않는다. 기존 테스트가 넘기던
    data_session/ai_session 은 이 헬퍼가 `patch_node_sessions` 로 ctx 에 주입한다.
    """
    with patch_node_sessions(data_session=data_session, ai_session=ai_session):
        return await graph.run(state)


def stream_graph(graph, state, *, data_session=None, ai_session=None):
    """graph.stream(state) 를 노드 로컬 세션 ctx 패치와 함께 감싸는 async generator.

    패치 컨텍스트가 스트림 소비 전 구간 동안 유지되도록 generator 로 감싼다.
    """

    async def _gen():
        with patch_node_sessions(data_session=data_session, ai_session=ai_session):
            async for ev in graph.stream(state):
                yield ev

    return _gen()


@contextmanager
def patch_node_sessions(
    *,
    data_session: Any = None,
    ai_session: Any = None,
    data_sessions: tuple[Any, ...] | None = None,
    ai_sessions: tuple[Any, ...] | None = None,
):
    """`agents.nodes` 와 `agents.vector_agent` 의 세션 ctx 를 mock 으로 패치한다.

    노드는 `data_session_ctx()`/`ai_session_ctx()` 로 세션을 잡으므로, 단위/통합
    테스트는 이 헬퍼로 mock 세션을 주입한다. graph.run()/stream() 은 더 이상 세션
    인자를 받지 않는다(0-6).

    제안 2 이후: VectorAgent.search() 가 `agents.vector_agent.ai_session_ctx()` 로
    채널별 세션을 독립 획득하므로, 해당 경로도 함께 패치한다.

    Args:
        data_session/ai_session: 매 acquire 마다 반환할 단일 mock 세션.
        data_sessions/ai_sessions: acquire 순서대로 소비할 mock 세션 튜플
            (retry 재진입 등 동일 노드가 세션을 재획득하는 경로 검증용).

    Yields:
        (data_ctx, ai_ctx) — `.used` 속성으로 실제 yield 된 세션 리스트를 관측한다.
    """
    d = _ctx_factory(*(data_sessions or ((data_session,) if data_session else ())))
    a = _ctx_factory(*(ai_sessions or ((ai_session,) if ai_session else ())))
    with (
        patch("agents._ondata_gateway.data_session_ctx", d),
        patch("agents._onai_gateway.ai_session_ctx", a),
        patch("agents.vector_agent.ai_session_ctx", a),
    ):
        yield d, a
