"""AgentGraph stream() / AgentState 입출력 계약 / 비스트리밍 emit no-op 테스트.

test_graph.py 분할 산출 — TestAgentStateContract / TestAgentGraphStream /
TestRunNonStreamingEmitNoop.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from agents.router_agent import RouterAgent
from core.config import settings
from schemas.intake import IntakeAction, TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_analytics_agent,
    make_intake,
    make_intake_router,
    run_graph,
    stream_graph,
)
from tests._graph_support import (
    _ai_session,
    _answer_agent,
    _router,
    _sql_agent,
    _state,
    _vector_agent,
)


# ---------------------------------------------------------------------------
# 4. AgentState 입출력 계약 (workflow.py와 동일)
# ---------------------------------------------------------------------------


class TestAgentStateContract:
    async def test_initial_fields_preserved(self):
        """run() 실행 후 room_id, message_id, message가 보존된다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(room_id=99, message_id=77, message="테스트"),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert result["room_id"] == 99
        assert result["message_id"] == 77
        assert result["message"] == "테스트"

    async def test_result_has_all_typed_fields(self):
        """run() 결과 state에 AgentState의 모든 키(retry_count 포함)가 존재한다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        expected_keys = {
            # 평면 채널
            "room_id",
            "message_id",
            "message",
            "title_needed",
            "user_lat",
            "user_lng",
            "trace",
            "error",
            "retry_count",
            # 도메인 working state (중첩 채널)
            "plan",
            "filters",
            "triage",
            "sql",
            "vector",
            "map",
            "analytics",
            "hydration",
            "output",
            "emit",
        }
        assert expected_keys <= set(result.keys())

    async def test_error_sets_fallback_answer(self):
        """Router 예외 시 error 필드와 fallback 답변이 채워진다."""
        router_agent = RouterAgent.__new__(RouterAgent)
        structured = MagicMock()

        def _raise(*_a, **_kw):
            raise RuntimeError("일시적 LLM 오류")

        structured.ainvoke = AsyncMock(side_effect=_raise)
        llm = MagicMock()
        llm.with_structured_output = MagicMock(return_value=structured)
        router_agent._llm = llm

        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=router_agent,
            answer_agent=_answer_agent(),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        # router 예외 시 fallback answer가 설정되며, error 없이 정상 종료된다
        assert result["output"]["answer"] is not None
        assert len(result["output"]["answer"]) > 0

    async def test_answer_path_does_not_set_title(self):
        """제목 생성은 generate_title_node 로 분리됐다 — answer 경로는 title 미설정.

        (title 이벤트 emit 회귀는 test_generate_title_node.py 가 커버한다.)
        """
        _, data_session = _sql_agent([])
        answer_agent = _answer_agent()

        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=answer_agent,
        )
        result = await run_graph(
            graph,
            _state(title_needed=True),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        assert "title" not in result["output"]


# ---------------------------------------------------------------------------
# 5. stream() 검증
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. stream() 검증
# ---------------------------------------------------------------------------


class TestAgentGraphStream:
    async def _collect(self, gen) -> list[tuple[str, object]]:
        events = []
        async for event_type, data in gen:
            events.append((event_type, data))
        return events

    async def test_stream_result_has_answer(self):
        """stream() result 이벤트에 answer가 채워진다."""
        _, data_session = _sql_agent([])
        graph = AgentGraph(
            router=_router(IntentType.FALLBACK),
            answer_agent=_answer_agent("스트림 답변"),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        result_events = [(t, d) for t, d in events if t == "result"]
        assert len(result_events) == 1
        _, result = result_events[0]
        assert result["output"]["answer"] == "스트림 답변"

    async def test_stream_result_matches_run_on_reduced_state(self):
        """stream() 최종 result 가 run()(ainvoke) 결과와 reducer 누적 필드에서 일치한다.

        회귀(작업 2): stream() 이 노드별 델타를 수동 합산하면 node_path
        (append reducer) / search_channels (or_ 병합 reducer) 가 마지막 델타로
        덮어써져 누적이 깨진다. 멀티모드 "values" 스냅샷을 쓰면 LangGraph 가
        reducer 를 적용한 전체 state 와 동일해야 한다.
        """
        rows = [
            {"service_id": "S1", "service_name": "수영장", "service_url": "https://x"},
        ]

        run_agent, run_ds = _sql_agent(rows)
        run_graph_obj = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=run_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        run_result = await run_graph(
            run_graph_obj,
            _state(),
            data_session=run_ds,
            ai_session=_ai_session(),
        )

        stream_agent, stream_ds = _sql_agent(rows)
        stream_graph_obj = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=stream_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        events = await self._collect(
            stream_graph(
                stream_graph_obj,
                _state(),
                data_session=stream_ds,
                ai_session=_ai_session(),
            )
        )
        result_events = [d for t, d in events if t == "result"]
        assert len(result_events) == 1
        stream_result = result_events[0]

        # node_path: append reducer 누적이 그대로 보존돼야 한다.
        assert stream_result["node_path"] == run_result["node_path"]
        assert len(stream_result["node_path"]) > 1  # 수동 덮어쓰기였다면 1로 붕괴
        # search_channels: or_ 병합 reducer 결과가 동일해야 한다.
        assert (
            stream_result["search_channels"].keys()
            == run_result["search_channels"].keys()
        )

    async def test_result_carries_service_cards_through_graph(self):
        """answer_node 가 AnswerAgent.service_cards 를 그래프 최종 state 로 전달한다.

        회귀: answer_node 래퍼가 answer/title 만 추출하고 service_cards 를
        누락하면, 단위 테스트(AnswerAgent.answer 직접 호출)는 통과해도 실제
        그래프 경로의 final payload 는 빈 배열이 된다. 이 통합 경로를 봉인한다.
        """
        rows = [
            {
                "service_id": "S001",
                "service_name": "수영장",
                "service_url": "https://x",
            },
            {
                "service_id": "S002",
                "service_name": "테니스장",
                "service_url": "https://y",
            },
        ]
        sql_agent, data_session = _sql_agent(rows)
        graph = AgentGraph(
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(),
            data_session=data_session,
            ai_session=_ai_session(),
        )

        cards = result["output"].get("service_cards")
        assert cards, f"service_cards 가 그래프 최종 state 에 전달되지 않음: {cards!r}"
        assert {c["service_id"] for c in cards} == {"S001", "S002"}

    # 기본 progress 시퀀스(routing→searching→answering present/순서)는
    # test_secondary_intent_fanout_emits_answering_once 와
    # test_router_only_path_no_decision_but_answering_flows 가 더 강하게(순서+count)
    # 커버하므로 단순 present 케이스는 축소했다.

    async def test_stream_emits_re_searching_on_retry(self):
        """재시도(SQL 0건→VECTOR 전환) 시 re_searching progress 1회 + 검색/답변 이벤트 재흐름."""
        sql_agent, data_session = _sql_agent([])  # SQL 0건 → 재시도 유발
        vector_agent, ai_session, mock_bm25 = _vector_agent([])
        vrows = [{"service_id": "V9", "service_name": "체험관", "similarity": 0.8}]
        hydrated = [{"service_id": "V9", "service_name": "체험관"}]

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
                router=_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("체험관 안내입니다."),
            )
            events = await self._collect(
                stream_graph(
                    graph, _state(), data_session=data_session, ai_session=ai_session
                )
            )

        steps = [d["step"] for t, d in events if t == "progress"]
        assert steps.count("re_searching") == 1
        # re_searching 이후 전환 경로의 searching/answering 이벤트가 다시 흐른다.
        idx = steps.index("re_searching")
        assert "searching" in steps[idx + 1 :]
        assert "answering" in steps[idx + 1 :]

    async def test_analytics_route_emits_searching_progress(self):
        """ANALYTICS 경로도 router 에서 'searching' progress 를 방출한다 (MAJOR 2).

        회귀: ANALYTICS 가 searching intent 튜플에서 누락되면 LLM 추출 + DB 집계가
        진행 중인데도 router_node 에서 조기에 answering 을 방출한다.
        """
        analytics_agent, data_session = make_analytics_agent(
            [{"group_value": "강남구", "count": 4}], group_by="area_name"
        )
        graph = AgentGraph(
            router=_router(IntentType.ANALYTICS),
            analytics_agent=analytics_agent,
            answer_agent=_answer_agent(),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert "searching" in progress_steps
        # searching 은 answering 보다 먼저 방출돼야 한다 (조기 answering 회귀 방어).
        assert progress_steps.index("searching") < progress_steps.index("answering")

    async def test_secondary_intent_fanout_emits_answering_once(self):
        """secondary_intent 팬아웃(sql+vector 병렬)에서 answering progress 가 정확히 1회.

        회귀 방어: sql_node·vector_node 가 동일 super-step 에 병렬 실행될 때 둘 다 자체
        answering 을 emit 하면 2회 흐른다(또는 answering_emitted 가드 슬롯에 두 값이
        동시 기록되어 InvalidUpdateError). answering emit 을 합류 머지점 hydration_node
        단일 지점으로 옮겨, 팬아웃이 실제로 발생해도 1회만 흐르는지 검증한다.

        실제 팬아웃을 발생시키기 위해 cache_check 직후의 라우팅 함수를
        route_by_action_fanout(secondary_intent + enable_secondary_intent=True 시
        ["sql_node","vector_node"] 반환)으로 패치한다.
        """
        sql_agent, data_session = _sql_agent(
            [{"service_id": "S1", "service_name": "수영장"}], keyword="수영장"
        )
        vector_agent, ai_session, mock_bm25 = _vector_agent(
            [{"service_id": "V1", "service_name": "체험관", "similarity": 0.8}]
        )
        intake, router = make_intake_router(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            intent=IntentType.SQL_SEARCH,
            user_rationale="복합 검색",
            secondary_intent=IntentType.VECTOR_SEARCH,
        )
        hydrated = [{"service_id": "S1", "service_name": "수영장"}]

        with (
            patch(
                "agents.vector_agent.vector_search",
                AsyncMock(
                    return_value=[
                        {
                            "service_id": "V1",
                            "service_name": "체험관",
                            "similarity": 0.8,
                        }
                    ]
                ),
            ),
            patch("agents.vector_agent.question_search", AsyncMock(return_value=[])),
            patch("agents.vector_agent.bm25_search", mock_bm25),
            patch(
                "agents.hydration_node.hydrate_services",
                AsyncMock(return_value=hydrated),
            ),
            # 실제 그래프에 와이어된 라우팅 함수를 팬아웃 분기로 교체해 sql+vector 병렬화.
            patch.object(
                GraphNodes, "post_cache_check", GraphNodes.route_by_action_fanout
            ),
            patch.object(settings, "enable_secondary_intent", True),
            patch.object(settings, "rrf_k_constant", 60),
            patch.object(settings, "rrf_top_k_final", 10),
        ):
            graph = AgentGraph(
                intake=intake,
                router=router,
                sql_agent=sql_agent,
                vector_agent=vector_agent,
                answer_agent=_answer_agent("복합 안내입니다."),
            )
            events = await self._collect(
                stream_graph(
                    graph,
                    _state(message="마포구 풋살장 알려줘"),
                    data_session=data_session,
                    ai_session=ai_session,
                )
            )

        progress_steps = [d["step"] for t, d in events if t == "progress"]
        # 팬아웃에서 sql_node·vector_node 가 둘 다 실행됐는지(병렬 super-step) 확인.
        result = [c for t, c in events if t == "result"][0]
        node_path = result.get("node_path") or []
        assert "sql_node" in node_path
        assert "vector_node" in node_path
        # answering 은 합류점 hydration_node 에서만 emit — 정확히 1회.
        assert progress_steps.count("answering") == 1
        # 관측 가능한 시퀀스는 기존과 동일: routing → searching → answering.
        assert progress_steps.index("routing") < progress_steps.index("searching")
        assert progress_steps.index("searching") < progress_steps.index("answering")

    async def test_attribute_gap_path_emits_answering_once(self):
        """OUT_OF_SCOPE/attribute_gap → vector_node → hydration_node 경로에서
        answering progress 가 정확히 1회.

        QA 회귀(작업 3): attribute_gap 은 triage 가 RETRIEVE 가 아니라 OUT_OF_SCOPE 를
        산출하므로 router_node 의 RETRIEVE emit 경로를 타지 않고, out_of_scope_node 가
        intent=VECTOR_SEARCH 로 vector_node 를 거쳐 hydration_node 로 합류한다. 이 경로의
        answering 도 단일 머지점 hydration_node 에서만 1회 흘러야 한다(vector_node 자체
        emit 으로 중복되거나, triage 비-RETRIEVE 분기가 추가 answering 을 넣어 2회가
        되면 안 됨).
        """
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.OUT_OF_SCOPE,
            oos_type="attribute_gap",
            user_rationale="속성 질의 — 시설 안내로 전환합니다.",
        )
        vrows = [
            {
                "service_id": "V001",
                "service_name": "마루공원 테니스장",
                "similarity": 0.9,
            }
        ]
        hydrated = [
            {
                "service_id": "V001",
                "service_name": "마루공원 테니스장",
                "service_url": "https://example.com",
            }
        ]
        vector_agent, ai_session, mock_bm25 = _vector_agent(vrows)

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
                vector_agent=vector_agent,
                answer_agent=_answer_agent("시설 페이지를 확인하세요."),
            )
            events = await self._collect(
                stream_graph(
                    graph,
                    _state(message="마루공원 테니스장 보수 공사"),
                    data_session=MagicMock(),
                    ai_session=ai_session,
                )
            )

        result = [c for t, c in events if t == "result"][0]
        node_path = result.get("node_path") or []
        # 경로 전제: vector_node 를 거쳐 hydration_node 로 합류했는지 확인.
        assert "vector_node" in node_path
        assert "hydration_node" in node_path
        progress_steps = [d["step"] for t, d in events if t == "progress"]
        # attribute_gap 도 answering 은 합류점에서 1회만.
        assert progress_steps.count("answering") == 1, (
            f"attribute_gap 경로 answering 이 1회가 아님: {progress_steps}"
        )
        # 비-RETRIEVE(OUT_OF_SCOPE) 라도 user_rationale 이 있으면 decision(routes=[]) 1회.
        decision_events = [d for t, d in events if t == "decision"]
        assert len(decision_events) == 1
        assert decision_events[0]["routes"] == []

    async def test_router_only_path_no_decision_but_answering_flows(self):
        """rationale=None(router-only 하위호환·forced 경로): decision 미emit, 그러나
        progress(searching/answering)는 정상으로 흐른다.

        QA 회귀(작업 3): decision emit 가드(rationale 없으면 skip)가 progress emit 까지
        막아버리면 router-only 경로에서 사용자가 진행 표시를 못 본다. decision 만 빠지고
        searching→answering 시퀀스는 그대로여야 한다.
        """
        sql_agent, data_session = _sql_agent(
            [{"service_id": "S1", "service_name": "수영장", "service_url": "https://x"}]
        )
        # intake 가 rationale=None 으로 NEW+RETRIEVE 를 산출 → router 가 검색 계획만
        # 세우고 decision 은 미emit(rationale 게이트), progress 는 정상 흐름.
        intake = make_intake(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            user_rationale=None,
        )
        graph = AgentGraph(
            intake=intake,
            router=_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내입니다."),
        )
        events = await self._collect(
            stream_graph(
                graph, _state(), data_session=data_session, ai_session=_ai_session()
            )
        )

        decision_events = [d for t, d in events if t == "decision"]
        assert len(decision_events) == 0, "rationale=None 이면 decision 미emit"
        progress_steps = [d["step"] for t, d in events if t == "progress"]
        assert progress_steps.count("searching") == 1
        assert progress_steps.count("answering") == 1
        assert progress_steps.index("searching") < progress_steps.index("answering")


# ---------------------------------------------------------------------------
# 6. DB 세션 라우팅 검증 (SQL → data_session, Vector → ai_session)
# ---------------------------------------------------------------------------


# unknown step(컨텍스트 밖)도 writer=None 단락으로 동일하게 no-op(None) 반환이라
# test_emit_progress_is_noop_outside_context 와 같은 분기의 입력 순열로 축소했다.


class TestRunNonStreamingEmitNoop:
    """run()(비스트리밍 ainvoke) 경로에서 노드 내부 emit 이 no-op 으로 흡수되고
    그래프가 정상 결과를 내는지 검증.

    QA 회귀(작업 3): ainvoke 에는 stream_mode custom 이 없어 writer 가 no-op 기본값
    이다. 노드가 emit 을 호출해도 결과 state 에 영향이 없어야 하고 크래시도 없어야
    한다(stream_graph 와 동일 결과).
    """

    async def test_run_completes_with_emit_calls_as_noop(self):
        """RETRIEVE 경로를 run()(ainvoke)으로 돌려도 answer 가 정상으로 채워진다."""
        rows = [
            {"service_id": "S1", "service_name": "수영장", "service_url": "https://x"}
        ]
        intake, router = make_intake_router(
            turn_kind=TurnKind.NEW,
            action=IntakeAction.RETRIEVE,
            intent=IntentType.SQL_SEARCH,
            user_rationale="수영장 검색입니다.",
        )
        sql_agent, data_session = _sql_agent(rows)
        graph = AgentGraph(
            intake=intake,
            router=router,
            sql_agent=sql_agent,
            answer_agent=_answer_agent("수영장 안내입니다."),
        )
        result = await run_graph(
            graph, _state(), data_session=data_session, ai_session=_ai_session()
        )

        # emit 이 no-op 으로 흡수되어 정상 흐름 — answer 채워지고 가드 슬롯이 흐름 중
        # 정상 갱신됐는지 확인(크래시 없음 + decision/answering 단계 통과).
        assert result["output"]["answer"] == "수영장 안내입니다."
        assert result["emit"].get("answering_emitted") is True
        assert result["emit"].get("decision_emitted") is True
