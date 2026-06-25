"""그래프 통합 — route_intake 분기 (REFINE 재검색 / 폴백-NEW) E2E.

검증(intake-node-merge §6 통합):
- REFINE → working_set_refine_node → router(forced_intent) → 머지 필터 재검색.
- 폴백-NEW(분류 모호): 미지 turn_kind 주입 → NEW+RETRIEVE 경로 + breadcrumb.
- META → explain_node.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from schemas.intake import IntakeAction, IntakeOutput, TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_intake,
    make_router,
    make_sql_agent,
    run_graph,
)


def _state(**kw):
    return make_agent_state(**kw)


class TestRefineReSearch:
    async def test_refine_merges_filters_and_re_searches(self):
        """REFINE → working_set_refine(신규 제약 머지) → router(forced SQL) → SQL 재검색.

        직전 area_name=강남구 + 이번 발화 신규 payment_type=무료 가 effective filters 에
        둘 다 반영돼야 한다(MUST-FIX: 신규 제약 소실 회귀 방지).
        """
        rows = [{"service_id": "S1", "service_name": "강남 무료 수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        hydrated = [{"service_id": "S1", "service_name": "강남 무료 수영장"}]

        ws = {
            "entities": [{"service_id": "S0", "label": "강남 수영장"}],
            "intent": IntentType.SQL_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        with patch(
            "agents.hydration_node.hydrate_services", AsyncMock(return_value=hydrated)
        ):
            graph = AgentGraph(
                intake=make_intake(turn_kind=TurnKind.REFINE),
                # 이번 발화("그 중 무료만")에서 router 가 신규 제약 payment_type=무료 추출.
                router=make_router(IntentType.SQL_SEARCH, payment_type="무료"),
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("강남 무료 수영장 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="그 중 무료만", prev_working_set=ws),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        path = result["node_path"]
        assert "working_set_refine" in path
        assert "router" in path
        assert "sql_node" in path
        # 베이스 필터(강남구)가 working_set_refine 에서 깔렸다.
        assert result["filters"].get("area_name") == "강남구"
        # ★ MUST-FIX: 이번 발화 신규 제약(payment_type=무료)이 effective filters 에 머지됐다.
        assert result["filters"].get("payment_type") == "무료"
        # forced_intent 가 router 에서 소비되어 SQL 경로로 갔다.
        assert result["plan"]["intent"] == IntentType.SQL_SEARCH


class TestMetaPath:
    async def test_meta_routes_to_explain(self):
        graph = AgentGraph(
            intake=make_intake(turn_kind=TurnKind.META, user_rationale="근거 설명"),
            answer_agent=make_answer_agent("판단 근거를 설명드립니다."),
        )
        result = await run_graph(
            graph,
            _state(message="왜 그렇게 판단했어?", prev_reasoning="자연 키워드 매칭"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )
        assert "explain_node" in result["node_path"]
        assert result["output"]["answer"]


class TestClassificationAmbiguityFallback:
    async def test_unknown_turn_kind_degrades_to_new_retrieve(self):
        """분류 모호(§2.5-A): 미지 turn_kind 주입 → NEW+RETRIEVE + breadcrumb.

        intake LLM 이 IntakeOutput 스키마 밖 값을 낼 수는 없으나, _build_update 의
        방어 분기가 NEW 로 강등하는지 확인한다(미지 enum 시뮬레이션).
        """
        intake = make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)
        # IntakeOutput.turn_kind 를 비-enum 으로 변조해 분류 모호 폴백을 유도.
        bad = IntakeOutput(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)
        object.__setattr__(bad, "turn_kind", "UNKNOWN_KIND")
        intake._llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=bad
        )

        rows = [{"service_id": "S1", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        with patch(
            "agents.hydration_node.hydrate_services",
            AsyncMock(return_value=[{"service_id": "S1"}]),
        ):
            graph = AgentGraph(
                intake=intake,
                router=make_router(IntentType.SQL_SEARCH),
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("수영장 안내입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="x"),
                data_session=data_session,
                ai_session=make_ai_session(),
            )

        assert result["triage"]["turn_kind"] == "NEW"
        assert "intake_route_fallback" in result["node_path"]
        # NEW+RETRIEVE 로 검색 경로 진입(환각 바인딩 0).
        assert "router" in result["node_path"]
        assert result.get("target_service_ids") is None
