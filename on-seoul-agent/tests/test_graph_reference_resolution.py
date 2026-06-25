"""그래프 수준 테스트 — 입구 단일화 후 참조(DRILL/RELEVANCE) 경로·재-hydrate·describe.

입구 단일화(intake_node)로 참조 바인딩은 규칙이 아니라 intake LLM 의 인덱스 선택으로
이뤄진다. 이 테스트는 intake 가 turn_kind=DRILL/RELEVANCE + ref_indices 를 산출했을 때:
- target_service_ids 바인딩(범위검증 순수 함수) + 검색(router/cache/sql) 스킵 + 재-hydrate
- 화제 전환(turn_kind=NEW) → 과잉 carryover 없음(router 흐름)
- describe-known-entity → 예약 카드 템플릿이 아닌 설명 경로 도달
- 재-hydrate 0건/DB 오류/LLM 오류 → 정직 안내·graceful degrade
- 하위호환: prev_entities 미전송 시 NEW 흐름
를 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from schemas.intake import IntakeAction, TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_intake,
    make_router,
    make_sql_agent,
    run_graph,
    stream_graph,
)

_PREV = [
    {"service_id": "S1", "label": "마루공원 테니스장"},
    {"service_id": "S2", "label": "강남구민체육센터 수영장"},
    {"service_id": "S3", "label": "마포 청소년 코딩 강좌"},
]


def _state(**kw):
    return make_agent_state(**kw)


def _drill(ref_indices):
    """DRILL turn_kind 으로 ref_indices 를 바인딩하는 intake mock."""
    return make_intake(turn_kind=TurnKind.DRILL, ref_indices=ref_indices)


def _new():
    return make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE)


class TestReferentialPath:
    async def test_drill_binds_and_skips_search(self):
        """DRILL(1번) → target_service_ids 바인딩 + 검색 스킵 + 재-hydrate."""
        hydrated = [
            {
                "service_id": "S1",
                "service_name": "마루공원 테니스장",
                "area_name": "노원구",
                "service_status": "접수중",
            }
        ]
        router = make_router(IntentType.SQL_SEARCH)
        router.classify = AsyncMock(side_effect=AssertionError("router must be skipped"))
        sql_agent, data_session = make_sql_agent([])

        with patch(
            "agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                intake=_drill([1]),
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("마루공원 테니스장은 노원구의 테니스 시설입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="첫 번째 어떤 곳이야?", prev_entities=_PREV),
                data_session=data_session,
                ai_session=MagicMock(),
            )

        assert result["target_service_ids"] == ["S1"]
        mock_hydrate.assert_awaited_once()
        assert mock_hydrate.await_args.args[1] == ["S1"]
        assert result["sql"].get("results") is None
        assert result["output"]["answer"] == "마루공원 테니스장은 노원구의 테니스 시설입니다."
        assert "intake" in result["node_path"]
        assert "rehydrate_node" in result["node_path"]
        assert "describe_node" in result["node_path"]
        assert "router" not in result["node_path"]

    async def test_index_binding(self):
        """DRILL(3번) → 세 번째 엔티티(S3) 재-hydrate."""
        hydrated = [{"service_id": "S3", "service_name": "마포 청소년 코딩 강좌"}]
        with patch(
            "agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                intake=_drill([3]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("코딩 강좌 설명입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="세번째 어떤 곳이야", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert result["target_service_ids"] == ["S3"]
        assert mock_hydrate.await_args.args[1] == ["S3"]

    async def test_describe_known_entity_cards(self):
        """describe-known-entity → 재-hydrate 원본이 service_cards 로 노출(설명 경로)."""
        hydrated = [
            {
                "service_id": "S1",
                "service_name": "마루공원 테니스장",
                "area_name": "노원구",
                "service_status": "접수중",
                "service_url": "https://yeyak.seoul.go.kr/x",
            }
        ]
        with patch("agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
                intake=_drill([1]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("마루공원 테니스장은 노원구 시설입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="방금 그거 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        cards = result["output"]["service_cards"]
        assert len(cards) == 1
        assert cards[0]["service_id"] == "S1"
        assert cards[0]["service_name"] == "마루공원 테니스장"

    async def test_rehydrate_zero_hits_honest_notice(self):
        """재-hydrate 0건(삭제/마감) → 빈 카드 + 정직 안내(환각 금지)."""
        with patch("agents._ondata_gateway._hydrate_services", AsyncMock(return_value=[])):
            graph = AgentGraph(
                intake=_drill([1]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent(
                    "방금 안내드린 시설의 최신 정보를 지금은 확인하기 어렵습니다. 다시 검색해 드릴까요?"
                ),
            )
            result = await run_graph(
                graph,
                _state(message="이 곳 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert result["target_service_ids"] == ["S1"]
        assert result["output"]["service_cards"] == []
        assert result["output"]["answer"]
        assert "describe_node" in result["node_path"]

    async def test_multi_reference_binding(self):
        """RELEVANCE 집합 참조(1,3번) → 복수 service_id 바인딩."""
        hydrated = [
            {"service_id": "S1", "service_name": "마루공원 테니스장"},
            {"service_id": "S3", "service_name": "마포 청소년 코딩 강좌"},
        ]
        with patch(
            "agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                intake=make_intake(turn_kind=TurnKind.RELEVANCE, ref_indices=[1, 3]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("두 시설을 설명드립니다."),
            )
            result = await run_graph(
                graph,
                _state(message="1번이랑 3번 비교해줘", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert result["target_service_ids"] == ["S1", "S3"]
        assert mock_hydrate.await_args.args[1] == ["S1", "S3"]
        assert len(result["output"]["service_cards"]) == 2

    async def test_partial_hydrate_only_existing_cards(self):
        """3건 참조했으나 1건만 hydrate(2건 soft-delete) → 존재하는 카드만 노출."""
        hydrated = [{"service_id": "S2", "service_name": "강남구민체육센터 수영장"}]
        with patch(
            "agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                intake=make_intake(turn_kind=TurnKind.RELEVANCE, ref_indices=[1, 2, 3]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("수영장 한 곳 설명입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="1번 2번 3번 비교", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert result["target_service_ids"] == ["S1", "S2", "S3"]
        assert mock_hydrate.await_args.args[1] == ["S1", "S2", "S3"]
        cards = result["output"]["service_cards"]
        assert len(cards) == 1
        assert cards[0]["service_id"] == "S2"
        assert "describe_node" in result["node_path"]

    async def test_rehydrate_uses_node_local_data_session(self):
        """rehydrate_node 가 data_session_ctx 로 세션을 acquire-use-release."""
        from tests.helpers import patch_node_sessions

        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        data_sess = MagicMock()
        captured_session = []

        async def _fake_hydrate(session, ids):
            captured_session.append(session)
            return hydrated

        graph = AgentGraph(
            intake=_drill([1]),
            router=make_router(IntentType.SQL_SEARCH),
            answer_agent=make_answer_agent("설명입니다."),
        )
        with (
            patch("agents._ondata_gateway._hydrate_services", _fake_hydrate),
            patch_node_sessions(data_session=data_sess, ai_session=MagicMock()) as (
                d_ctx,
                _a_ctx,
            ),
        ):
            await graph.run(_state(message="이 곳 어떤 곳이야?", prev_entities=_PREV))
        assert captured_session == [data_sess]
        assert data_sess in d_ctx.used  # type: ignore[attr-defined]

    async def test_rehydrate_db_error_falls_back_to_empty_describe(self):
        """재-hydrate 중 DB 오류 → hydrated=[] 폴백 + describe 정직 안내."""
        with patch(
            "agents._ondata_gateway._hydrate_services",
            AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            graph = AgentGraph(
                intake=_drill([1]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent(
                    "방금 안내드린 시설의 최신 정보를 지금은 확인하기 어렵습니다."
                ),
            )
            result = await run_graph(
                graph,
                _state(message="이 곳 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert result["output"]["service_cards"] == []
        assert result["output"]["answer"]
        assert "rehydrate_error" in result["node_path"]
        assert "describe_node" in result["node_path"]

    async def test_describe_llm_error_yields_graceful_fallback(self):
        """describe()(LLM) 자체가 예외 → describe_error + 안내 폴백."""
        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        answer_agent = make_answer_agent("쓰이지 않음")
        answer_agent.describe = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch("agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
                intake=_drill([1]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                _state(message="이 곳 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        assert "describe_error" in result["node_path"]
        assert result["output"]["answer"]
        assert result.get("error")


class TestReferentialStreamEvents:
    async def test_referential_stream_emits_routing_then_answering_no_searching(self):
        """참조 경로 stream → routing → answering 만, searching 미발생."""
        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        with patch("agents._ondata_gateway._hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
                intake=_drill([1]),
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("설명입니다."),
            )
            events = []
            async for ev in stream_graph(
                graph,
                _state(message="이 곳 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            ):
                events.append(ev)

        steps = [e[1]["step"] for e in events if e[0] == "progress"]
        assert steps == ["routing", "answering"]
        assert "searching" not in steps
        result = next(e[1] for e in events if e[0] == "result")
        assert result["target_service_ids"] == ["S1"]


class TestNonReferentialBackcompat:
    async def test_topic_switch_uses_router(self):
        """화제 전환(turn_kind=NEW) → 과잉 carryover 없이 router 흐름."""
        rows = [{"service_id": "N1", "service_name": "풋살장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            intake=_new(),
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("풋살장 안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(message="강남구 풋살장 알려줘", prev_entities=_PREV),
            data_session=data_session,
            ai_session=MagicMock(),
        )
        assert result.get("target_service_ids") is None
        assert result["plan"]["intent"] == IntentType.SQL_SEARCH
        assert "router" in result["node_path"]
        assert "describe_node" not in result["node_path"]
        assert any(r["service_id"] == "N1" for r in result["sql"]["results"])

    async def test_no_prev_entities_is_backcompat(self):
        """prev_entities 미전송 → NEW 흐름(router)."""
        rows = [{"service_id": "N1", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            intake=_new(),
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("수영장 안내입니다."),
        )
        result = await run_graph(
            graph,
            _state(message="이 곳이 어떤 곳이야?"),  # prev_entities=None
            data_session=data_session,
            ai_session=MagicMock(),
        )
        assert result.get("target_service_ids") is None
        assert result["plan"]["intent"] == IntentType.SQL_SEARCH
        assert "describe_node" not in result["node_path"]
