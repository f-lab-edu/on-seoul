"""L1 retrieval-critic 관측 + SSE 테스트.

검증 대상:
  1. CriticDecisionEvent 스키마 (필드/타입/event literal 고정).
  2. emit_critic_decision: custom stream 으로 `_evt=critic_decision` 페이로드 흘려보냄.
  3. _observe_critic: critic 결정을 sanitize 후 SSE emit + Langfuse span 기록.
     · rationale=None/빈값이면 emit 스킵(triage decision 가드와 동형).
     · 내부 식별자(줄머리 __) 제거(sanitize_user_rationale 재사용).
  4. E2E stream: critic 진입 시 critic_decision SSE + REPLAN 시 re_searching progress(라운드마다).
  5. 플래그 오프 불변(회귀 0): critic 미진입 → critic_decision 이벤트/스팬 0.
  6. Langfuse span best-effort: client 미활성/예외여도 그래프/emit 을 막지 않음.

모든 LLM 은 fake(structured-output mock)로 주입한다 — 실 호출 금지.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents._helpers import emit_critic_decision
from agents.graph import AgentGraph, record_critic_span
from agents.retrieval_critic import RetrievalCritic
from core.config import settings
from schemas.critic import CriticOutput, ReplanHint
from schemas.events import CriticDecisionEvent
from schemas.state import IntentType
from tests._graph_support import _ai_session, _sql_agent, _vector_agent
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_intake_router,
    stream_graph,
)


def _state(**kwargs):
    return make_agent_state(**kwargs)


def _make_critic(output: CriticOutput | None = None, *, raise_exc=None) -> RetrievalCritic:
    critic = RetrievalCritic.__new__(RetrievalCritic)
    structured = MagicMock()
    if raise_exc is not None:
        structured.ainvoke = AsyncMock(side_effect=raise_exc)
    else:
        structured.ainvoke = AsyncMock(return_value=output)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    critic._llm = llm
    return critic


# ---------------------------------------------------------------------------
# 1. CriticDecisionEvent 스키마
# ---------------------------------------------------------------------------


class TestCriticDecisionEventSchema:
    def test_event_literal_fixed(self):
        ev = CriticDecisionEvent(decision="REPLAN", round=0, user_rationale="다시 찾습니다.")
        assert ev.event == "critic_decision"

    def test_fields(self):
        ev = CriticDecisionEvent(decision="ANSWER", round=1, user_rationale="충분합니다.")
        assert ev.decision == "ANSWER"
        assert ev.round == 1
        assert ev.user_rationale == "충분합니다."

    def test_model_dump_json_serializable(self):
        import json

        ev = CriticDecisionEvent(decision="STOP", round=0, user_rationale="없습니다.")
        assert "critic_decision" in json.dumps(ev.model_dump())


# ---------------------------------------------------------------------------
# 2. emit_critic_decision (writer 페이로드)
# ---------------------------------------------------------------------------


class TestEmitCriticDecision:
    def test_writes_custom_payload(self):
        writer = MagicMock()
        with patch("agents._helpers._writer", return_value=writer):
            emit_critic_decision("REPLAN", 0, "조건을 바꿔 다시 찾습니다.")
        writer.assert_called_once()
        payload = writer.call_args.args[0]
        assert payload["_evt"] == "critic_decision"
        assert payload["decision"] == "REPLAN"
        assert payload["round"] == 0
        assert payload["user_rationale"] == "조건을 바꿔 다시 찾습니다."

    def test_no_writer_context_is_noop(self):
        """runnable 컨텍스트 밖(writer=None)이면 no-op — 예외 없음."""
        with patch("agents._helpers._writer", return_value=None):
            emit_critic_decision("ANSWER", 0, "x")  # 예외 없이 통과


# ---------------------------------------------------------------------------
# 3. _observe_critic (SSE emit + sanitize + span)
# ---------------------------------------------------------------------------


class TestObserveCritic:
    def test_emits_sanitized_rationale(self):
        from agents.nodes.retrieval import RetrievalNodes

        state = _state(retry_count=0)
        update = {
            "critic_decision": "REPLAN",
            "critic_rationale": "정형 필터가 과해 벡터로 다시 찾습니다.",
        }
        with (
            patch("agents.nodes.retrieval.emit_critic_decision") as emit_mock,
            patch("agents.graph.record_critic_span") as span_mock,
        ):
            RetrievalNodes._observe_critic(state, update, "zero")
        emit_mock.assert_called_once()
        args = emit_mock.call_args.args
        assert args[0] == "REPLAN"
        assert args[1] == 0  # round = retry_count
        assert args[2] == "정형 필터가 과해 벡터로 다시 찾습니다."
        span_mock.assert_called_once_with("zero", "REPLAN", 0)

    def test_strips_internal_identifier_lines(self):
        """줄머리 __ 내부 식별자 줄은 노출에서 제거된다(sanitize_user_rationale)."""
        from agents.nodes.retrieval import RetrievalNodes

        state = _state(retry_count=1)
        update = {
            "critic_decision": "ANSWER",
            "critic_rationale": "__service_id: S001\n정상 근거 문장입니다.",
        }
        with (
            patch("agents.nodes.retrieval.emit_critic_decision") as emit_mock,
            patch("agents.graph.record_critic_span"),
        ):
            RetrievalNodes._observe_critic(state, update, "thin")
        rationale = emit_mock.call_args.args[2]
        assert "__service_id" not in rationale
        assert "정상 근거 문장입니다." in rationale
        assert emit_mock.call_args.args[1] == 1  # round = retry_count

    def test_none_rationale_skips_emit_but_still_records_span(self):
        """rationale 없으면 SSE emit 스킵(triage decision 가드 동형). span 은 기록."""
        from agents.nodes.retrieval import RetrievalNodes

        state = _state(retry_count=0)
        update = {"critic_decision": "STOP", "critic_rationale": None}
        with (
            patch("agents.nodes.retrieval.emit_critic_decision") as emit_mock,
            patch("agents.graph.record_critic_span") as span_mock,
        ):
            RetrievalNodes._observe_critic(state, update, "zero")
        emit_mock.assert_not_called()
        span_mock.assert_called_once_with("zero", "STOP", 0)

    def test_fail_open_none_decision_skips_emit(self):
        """critic 미결정(decision=None) → emit 스킵, span 은 None decision 으로 기록."""
        from agents.nodes.retrieval import RetrievalNodes

        state = _state(retry_count=0)
        update = {"critic_decision": None, "critic_rationale": None}
        with (
            patch("agents.nodes.retrieval.emit_critic_decision") as emit_mock,
            patch("agents.graph.record_critic_span") as span_mock,
        ):
            RetrievalNodes._observe_critic(state, update, "skew")
        emit_mock.assert_not_called()
        span_mock.assert_called_once_with("skew", None, 0)

    def test_emit_exception_does_not_block_span(self):
        """SSE emit 예외가 span 기록/그래프를 막지 않는다(best-effort)."""
        from agents.nodes.retrieval import RetrievalNodes

        state = _state(retry_count=0)
        update = {"critic_decision": "ANSWER", "critic_rationale": "근거"}
        with (
            patch(
                "agents.nodes.retrieval.emit_critic_decision",
                side_effect=RuntimeError("emit boom"),
            ),
            patch("agents.graph.record_critic_span") as span_mock,
        ):
            RetrievalNodes._observe_critic(state, update, "zero")  # 예외 전파 없음
        span_mock.assert_called_once()


# ---------------------------------------------------------------------------
# 4. _entry_signal 분류
# ---------------------------------------------------------------------------


class TestEntrySignal:
    def _fn(self):
        from agents.nodes.retrieval import RetrievalNodes

        return RetrievalNodes._entry_signal

    def test_zero(self):
        assert self._fn()(_state(intent=IntentType.SQL_SEARCH, hydrated_services=[])) == "zero"

    def test_thin(self):
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": "S1"}],
            result_quality={"thin": True, "skew_field": None},
        )
        assert self._fn()(state) == "thin"

    def test_skew_only_not_escalated(self):
        """skew-만(0건·thin 아님)은 critic 승격 신호가 아니다 → None.

        skew 는 지역 미지정 시에만 산출되어 재검색으로 교정 불가하므로 answer 톤
        조정으로만 처리한다(critic 미진입).
        """
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": f"S{i}"} for i in range(4)],
            result_quality={"thin": False, "skew_field": "area_name", "skew_ratio": 0.9},
        )
        assert self._fn()(state) is None

    def test_clearly_good_none(self):
        state = _state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=[{"service_id": f"S{i}"} for i in range(5)],
            result_quality=None,
        )
        assert self._fn()(state) is None


# ---------------------------------------------------------------------------
# 5. record_critic_span (Langfuse best-effort)
# ---------------------------------------------------------------------------


class TestRecordCriticSpan:
    def test_active_client_records_span_metadata(self):
        span = MagicMock()
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=span)
        cm.__exit__ = MagicMock(return_value=False)
        client = MagicMock()
        client.start_as_current_observation = MagicMock(return_value=cm)

        with patch("core.langfuse_client.get_langfuse_client", return_value=client):
            record_critic_span("thin", "REPLAN", 1)

        obs_kwargs = client.start_as_current_observation.call_args.kwargs
        assert obs_kwargs["as_type"] == "span"
        assert obs_kwargs["name"] == "retrieval_critic"
        meta = span.update.call_args.kwargs["metadata"]
        assert meta == {"entry_signal": "thin", "decision": "REPLAN", "round": 1}

    def test_inactive_client_noop(self):
        with patch("core.langfuse_client.get_langfuse_client", return_value=None):
            record_critic_span("zero", "STOP", 0)  # 예외 없이 no-op

    def test_span_exception_swallowed(self):
        client = MagicMock()
        client.start_as_current_observation = MagicMock(side_effect=RuntimeError("boom"))
        with patch("core.langfuse_client.get_langfuse_client", return_value=client):
            record_critic_span("skew", "ANSWER", 0)  # 예외 전파 없음


# ---------------------------------------------------------------------------
# 6. E2E stream — critic_decision SSE + REPLAN re_searching progress
# ---------------------------------------------------------------------------


class TestCriticStreamEvents:
    async def _collect(self, graph, state, **kwargs):
        events = []
        async for ev in stream_graph(graph, state, **kwargs):
            events.append(ev)
        return events

    async def test_critic_decision_sse_on_stop(self):
        """0건 → critic STOP → critic_decision SSE 1회(sanitize 근거)."""
        sql_agent, data_session = _sql_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(
            CriticOutput(decision="STOP", rationale="조건에 맞는 서비스가 없습니다.")
        )
        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("결과가 없습니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                events = await self._collect(
                    graph, _state(), data_session=data_session, ai_session=_ai_session()
                )

        critic_events = [(t, d) for t, d in events if t == "critic_decision"]
        assert len(critic_events) == 1
        _, data = critic_events[0]
        assert data["event"] == "critic_decision"
        assert data["decision"] == "STOP"
        assert data["round"] == 0
        assert data["user_rationale"] == "조건에 맞는 서비스가 없습니다."

    async def test_replan_emits_re_searching_progress_per_round(self):
        """0건 → critic REPLAN → 재검색: critic_decision SSE + re_searching progress."""
        sql_agent, data_session = _sql_agent([])
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(
            CriticOutput(
                decision="REPLAN",
                replan_hint=ReplanHint(intent=IntentType.VECTOR_SEARCH, reason="정형 실패"),
                rationale="조건을 바꿔 다시 찾는 중입니다.",
            )
        )
        vrows = [
            {"service_id": f"V{i}", "service_name": f"체험관{i}", "similarity": 0.8}
            for i in range(4)
        ]
        hydrated = [{"service_id": f"V{i}", "service_name": f"체험관{i}"} for i in range(4)]

        with (
            patch("agents.vector_agent.vector_search", AsyncMock(return_value=vrows)),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=make_answer_agent("체험관 안내입니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                events = await self._collect(
                    graph, _state(), data_session=data_session, ai_session=ai_session
                )

        critic_events = [(t, d) for t, d in events if t == "critic_decision"]
        assert len(critic_events) == 1
        assert critic_events[0][1]["decision"] == "REPLAN"

        # REPLAN → retry_prep 이 라운드마다 re_searching progress 를 emit 한다.
        re_searching = [
            d for t, d in events if t == "progress" and d.get("step") == "re_searching"
        ]
        assert re_searching, "REPLAN 재검색 시 re_searching progress 가 있어야 한다"

    async def test_flag_off_emits_no_critic_events(self):
        """플래그 오프(기본, 회귀 0): critic 미진입 → critic_decision 이벤트 0."""
        sql_agent, data_session = _sql_agent([])
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(CriticOutput(decision="STOP", rationale="x"))
        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=[])
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("답변"),
                critic=critic,
            )
            # enable_retrieval_critic 기본(False) — patch 하지 않는다.
            events = await self._collect(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )

        critic_events = [(t, d) for t, d in events if t == "critic_decision"]
        assert critic_events == []
        # critic LLM 도 호출되지 않았다(escalation 게이트가 미진입).
        critic._llm.with_structured_output.return_value.ainvoke.assert_not_awaited()

    async def test_good_result_emits_no_critic_events(self):
        """명백히 좋은 결과(플래그 온) → critic 미호출 → critic_decision 이벤트 0."""
        rows = [{"service_id": f"S{i}", "service_name": f"수영장{i}"} for i in range(5)]
        sql_agent, data_session = _sql_agent(rows)
        intake, router = make_intake_router(intent=IntentType.SQL_SEARCH)
        critic = _make_critic(CriticOutput(decision="ANSWER", rationale="ok"))

        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=rows)
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("안내입니다."),
                critic=critic,
            )
            with patch.object(settings, "enable_retrieval_critic", True):
                events = await self._collect(
                    graph, _state(), data_session=data_session, ai_session=_ai_session()
                )

        assert [(t, d) for t, d in events if t == "critic_decision"] == []
