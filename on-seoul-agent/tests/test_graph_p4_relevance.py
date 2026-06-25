"""통합(그래프) — 사례 156 재현: 적합성 후속이 현재형 no-results 로 변질되지 않는다.

직전 턴 prev_entities 5건 + "왜 이 항목들이 자연속 활동이야?" → intake RELEVANCE →
인덱스 바인딩 → rehydrate → describe(적합성 변형) 경로 도달. 검색(router/sql) 스킵.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import _STRUCT_RELEVANCE
from agents.graph import AgentGraph
from schemas.intake import TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_answer_agent,
    make_intake,
    make_router,
    run_graph,
)

_PREV = [
    {"service_id": "S1", "label": "북한산 둘레길 탐방"},
    {"service_id": "S2", "label": "서울숲 공원탐방"},
    {"service_id": "S3", "label": "남산 자연학습"},
    {"service_id": "S4", "label": "관악산 숲길 걷기"},
    {"service_id": "S5", "label": "월드컵공원 생태탐방"},
]


class TestCase156RelevanceReproduction:
    async def test_relevance_follow_up_reaches_describe_not_no_results(self):
        hydrated = [
            {
                "service_id": "S1",
                "service_name": "북한산 둘레길 탐방",
                "min_class_name": "산림여가",
                "place_name": "북한산",
            },
            {
                "service_id": "S2",
                "service_name": "서울숲 공원탐방",
                "min_class_name": "공원탐방",
                "place_name": "서울숲",
            },
        ]
        router = make_router(IntentType.SQL_SEARCH)
        router.classify = AsyncMock(side_effect=AssertionError("router must be skipped"))

        with patch(
            "agents._ondata_gateway._hydrate_services",
            AsyncMock(return_value=hydrated),
        ) as mock_hydrate:
            answer_agent = make_answer_agent(
                "두 시설 모두 산림여가/공원탐방이라 자연 속 활동입니다."
            )
            graph = AgentGraph(
                intake=make_intake(turn_kind=TurnKind.RELEVANCE, ref_indices=[1, 2]),
                router=router,
                answer_agent=answer_agent,
            )
            result = await run_graph(
                graph,
                make_agent_state(
                    message="왜 이 항목들이 자연속 활동이야?",
                    prev_entities=_PREV,
                ),
                data_session=MagicMock(),
                ai_session=MagicMock(),
            )

        # 검색 스킵 + describe 경로 도달.
        assert result["target_service_ids"] == ["S1", "S2"]
        assert "router" not in result["node_path"]
        assert "describe_node" in result["node_path"]
        mock_hydrate.assert_awaited_once()

        # 적합성 변형 프롬프트가 실렸다(현재형 no-results 경로가 아님).
        call = answer_agent._answer_chain.ainvoke.call_args[0][0]
        assert _STRUCT_RELEVANCE[:30] in call["system"]
        # 직전 5건 중 2건이 카드로 노출(직전 결과 위에서 설명).
        assert len(result["output"]["service_cards"]) == 2
        # answer 가 "못 찾았다" no-results 가 아니다.
        assert result["output"]["answer"]
        assert "못 찾" not in result["output"]["answer"]
