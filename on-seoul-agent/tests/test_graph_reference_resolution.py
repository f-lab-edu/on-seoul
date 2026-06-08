"""W1 그래프 수준 테스트 — 참조 해소·재-hydrate·describe-known-entity.

검증:
- 지시 참조 → target_service_ids 바인딩 + 검색(router/cache/sql/vector) 스킵 + 재-hydrate
- 화제 전환(non-referential) → 과잉 carryover 없음(기존 router 흐름)
- describe-known-entity → 예약 카드 템플릿이 아닌 설명 경로 도달
- 재-hydrate 0건 → 정직 안내 + 카드 없음(환각·빈 카드 금지)
- 다중 참조("1번이랑 3번") 복수 바인딩
- 하위호환: prev_entities 미전송 시 기존 흐름과 동일
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import AgentGraph
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
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


class TestReferentialPath:
    async def test_demonstrative_binds_and_skips_search(self):
        """지시 참조 → target_service_ids 바인딩 + 검색 스킵 + 재-hydrate."""
        hydrated = [
            {
                "service_id": "S1",
                "service_name": "마루공원 테니스장",
                "area_name": "노원구",
                "service_status": "접수중",
            }
        ]
        # router/sql 이 호출되면 안 됨 — 호출되면 AssertionError 가 나도록 구성.
        router = make_router(IntentType.SQL_SEARCH)
        router.classify = AsyncMock(side_effect=AssertionError("router must be skipped"))
        sql_agent, data_session = make_sql_agent([])

        with patch(
            "agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                router=router,
                sql_agent=sql_agent,
                answer_agent=make_answer_agent("마루공원 테니스장은 노원구의 테니스 시설입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="이 곳이 어떤 곳이야?", prev_entities=_PREV),
                data_session=data_session,
                ai_session=MagicMock(),
            )

        assert result["target_service_ids"] == ["S1"]
        mock_hydrate.assert_awaited_once()
        # hydrate_services 에 바인딩된 service_id 가 전달됐는지
        assert mock_hydrate.await_args.args[1] == ["S1"]
        # 검색 슬롯은 채워지지 않음(검색 스킵)
        assert result.get("sql_results") is None
        assert result["answer"] == "마루공원 테니스장은 노원구의 테니스 시설입니다."
        # describe 경로 node_path 확인
        assert "reference_resolution" in result["node_path"]
        assert "rehydrate_node" in result["node_path"]
        assert "describe_node" in result["node_path"]
        # W2: router -> triage 교체; 참조 해소 경로는 triage도 거치지 않아야 함
        assert "router" not in result["node_path"]
        assert "triage" not in result["node_path"]

    async def test_ordinal_binding(self):
        """서수 참조('세번째') → 세 번째 엔티티(S3) 재-hydrate."""
        hydrated = [{"service_id": "S3", "service_name": "마포 청소년 코딩 강좌"}]
        with patch(
            "agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
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
        with patch("agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("마루공원 테니스장은 노원구 시설입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="방금 그거 어떤 곳이야?", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        cards = result["service_cards"]
        assert len(cards) == 1
        assert cards[0]["service_id"] == "S1"
        assert cards[0]["service_name"] == "마루공원 테니스장"

    async def test_rehydrate_zero_hits_honest_notice(self):
        """재-hydrate 0건(삭제/마감) → 빈 카드 + 정직 안내(환각 금지)."""
        with patch("agents.nodes.hydrate_services", AsyncMock(return_value=[])):
            graph = AgentGraph(
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
        assert result["service_cards"] == []
        assert result["answer"]  # 비어 있지 않은 정직 안내
        assert "describe_node" in result["node_path"]

    async def test_multi_reference_binding(self):
        """다중 참조('1번이랑 3번') → 복수 service_id 바인딩."""
        hydrated = [
            {"service_id": "S1", "service_name": "마루공원 테니스장"},
            {"service_id": "S3", "service_name": "마포 청소년 코딩 강좌"},
        ]
        with patch(
            "agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
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
        assert len(result["service_cards"]) == 2


    async def test_partial_hydrate_only_existing_cards(self):
        """QA 갭: 3건 참조했으나 1건만 hydrate(2건 soft-delete) → 존재하는 카드만 노출.

        target_service_ids 는 요청한 3건 모두 바인딩되지만, 재-hydrate 가 1건만
        반환하면 service_cards 는 1건만 노출한다(없는 시설의 환각 카드 금지).
        """
        # 요청 3건 중 S2 만 살아있음(S1/S3 마감·삭제).
        hydrated = [{"service_id": "S2", "service_name": "강남구민체육센터 수영장"}]
        with patch(
            "agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)
        ) as mock_hydrate:
            graph = AgentGraph(
                router=make_router(IntentType.SQL_SEARCH),
                answer_agent=make_answer_agent("수영장 한 곳 설명입니다."),
            )
            result = await run_graph(
                graph,
                _state(message="1번 2번 3번 비교", prev_entities=_PREV),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )
        # 3건 모두 바인딩(요청)
        assert result["target_service_ids"] == ["S1", "S2", "S3"]
        assert mock_hydrate.await_args.args[1] == ["S1", "S2", "S3"]
        # 그러나 카드는 실제 hydrate 된 1건만 — 환각 카드 없음
        cards = result["service_cards"]
        assert len(cards) == 1
        assert cards[0]["service_id"] == "S2"
        assert "describe_node" in result["node_path"]

    async def test_rehydrate_uses_node_local_data_session(self):
        """QA 갭: rehydrate_node 가 data_session_ctx 로 세션을 acquire-use-release.

        패치된 ctx 의 .used 로 세션이 정확히 1회 잡혀 hydrate_services 에 전달되는지
        확인한다(노드 로컬 세션 — 인스턴스 누수/오용 없음).
        """
        from tests.helpers import patch_node_sessions

        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        data_sess = MagicMock()
        captured_session = []

        async def _fake_hydrate(session, ids):
            captured_session.append(session)
            return hydrated

        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            answer_agent=make_answer_agent("설명입니다."),
        )
        with (
            patch("agents.nodes.hydrate_services", _fake_hydrate),
            patch_node_sessions(data_session=data_sess, ai_session=MagicMock()) as (
                d_ctx,
                _a_ctx,
            ),
        ):
            await graph.run(
                _state(message="이 곳 어떤 곳이야?", prev_entities=_PREV)
            )
        # rehydrate_node 가 data_session_ctx 를 잡아 hydrate_services 에 전달했는지
        assert captured_session == [data_sess]
        # ctx 가 실제로 acquire 되었는지(.used 에 기록)
        assert data_sess in d_ctx.used  # type: ignore[attr-defined]

    async def test_rehydrate_db_error_falls_back_to_empty_describe(self):
        """QA 갭: 재-hydrate 중 DB 오류 → hydrated=[] 폴백 + describe 정직 안내.

        downstream(DB) 실패 시 500 으로 새지 않고 빈 카드 + 안내로 graceful degrade.
        """
        with patch(
            "agents.nodes.hydrate_services",
            AsyncMock(side_effect=RuntimeError("DB down")),
        ):
            graph = AgentGraph(
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
        assert result["service_cards"] == []
        assert result["answer"]  # 정직 안내 — 비어 있지 않음
        assert "rehydrate_error" in result["node_path"]
        assert "describe_node" in result["node_path"]


    async def test_describe_llm_error_yields_graceful_fallback(self):
        """QA 갭: describe()(LLM) 자체가 예외 → describe_error + 안내 폴백.

        downstream LLM 500 등으로 describe 가 던져도 500 으로 새지 않고 친절 안내
        답변 + describe_error node_path 로 graceful degrade 한다.
        """
        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        answer_agent = make_answer_agent("쓰이지 않음")
        answer_agent.describe = AsyncMock(side_effect=RuntimeError("LLM down"))
        with patch("agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
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
        assert result["answer"]  # 친절 안내 폴백
        assert result.get("error")


class TestReferentialStreamEvents:
    async def test_referential_stream_emits_routing_then_answering_no_searching(self):
        """QA 갭: 참조 경로 stream → routing → answering 만, searching 미발생.

        rehydrate_node 가 신규 SSE 이벤트 없이 기존 "answering" 이벤트만 emit 하는지
        (하위호환) 확인한다. 검색 노드를 우회하므로 "searching" 이벤트는 없어야 한다.
        """
        hydrated = [{"service_id": "S1", "service_name": "마루공원 테니스장"}]
        with patch("agents.nodes.hydrate_services", AsyncMock(return_value=hydrated)):
            graph = AgentGraph(
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
        # 최종 result 이벤트 존재 + describe 경로 결과
        result = next(e[1] for e in events if e[0] == "result")
        assert result["target_service_ids"] == ["S1"]


class TestNonReferentialBackcompat:
    async def test_topic_switch_uses_router(self):
        """화제 전환(non-referential) → 과잉 carryover 없이 기존 router 흐름."""
        rows = [{"service_id": "N1", "service_name": "풋살장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("풋살장 안내입니다."),
        )
        # prev_entities 가 있어도 지시어/서수/라벨 매칭이 없으면 non-referential.
        result = await run_graph(
            graph,
            _state(message="강남구 풋살장 알려줘", prev_entities=_PREV),
            data_session=data_session,
            ai_session=MagicMock(),
        )
        assert result.get("target_service_ids") is None
        assert result["intent"] == IntentType.SQL_SEARCH
        # W2: router -> triage 교체; 하위호환 alias "router"도 허용
        assert "triage" in result["node_path"] or "router" in result["node_path"]
        assert "describe_node" not in result["node_path"]
        assert any(r["service_id"] == "N1" for r in result["sql_results"])

    async def test_topic_switch_shared_region_prefix_uses_router(self):
        """MUST-FIX 회귀: 라벨과 자치구 prefix("마포")만 공유하는 화제 전환은
        carryover 되지 않고 router 흐름을 탄다(describe_node 우회 금지)."""
        rows = [{"service_id": "N9", "service_name": "마포 수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
            router=make_router(IntentType.SQL_SEARCH),
            sql_agent=sql_agent,
            answer_agent=make_answer_agent("마포 수영장 안내입니다."),
        )
        result = await run_graph(
            graph,
            # _PREV[2] = {"service_id": "S3", "label": "마포 청소년 코딩 강좌"}
            _state(message="마포 수영장 알려줘", prev_entities=_PREV),
            data_session=data_session,
            ai_session=MagicMock(),
        )
        assert result.get("target_service_ids") is None
        assert result["intent"] == IntentType.SQL_SEARCH
        # W2: router -> triage 교체; 하위호환 alias "router"도 허용
        assert "triage" in result["node_path"] or "router" in result["node_path"]
        assert "describe_node" not in result["node_path"]

    async def test_no_prev_entities_is_backcompat(self):
        """prev_entities 미전송 → 기존 흐름과 동일(지시어 있어도 non-referential)."""
        rows = [{"service_id": "N1", "service_name": "수영장"}]
        sql_agent, data_session = make_sql_agent(rows)
        graph = AgentGraph(
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
        assert result["intent"] == IntentType.SQL_SEARCH
        assert "describe_node" not in result["node_path"]
