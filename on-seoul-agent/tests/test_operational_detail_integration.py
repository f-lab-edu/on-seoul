"""운영-상세 통합 테스트 — 사례 162-163 재현.

"마루공원 테니스장 폭염철 이용안내" + 해당 service detail_content 에 폭염 텍스트
(섹션4) 존재 → 거짓 단정/도메인 거절 미발생, 섹션4 포함 발췌 기반 안내 도달.

operational_detail → out_of_scope_node → vector_node → hydration → pre_answer_gate
(focal fetch + 발췌) → answer(운영-상세 프롬프트). fake LLM / fake session(hermetic).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import AnswerAgent, _STRUCT_OPERATIONAL_DETAIL
from agents.graph import AgentGraph
from agents._ondata_gateway import OnDataReader
from schemas.intake import IntakeAction, TurnKind
from schemas.state import IntentType
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_intake,
    make_router,
    run_graph,
)

# 섹션4(4. 주의사항) 후반에 폭염 운영지침이 위치한 detail_content.
_DETAIL = (
    "1. 필수 준수사항\n예약 보일러플레이트입니다.\n"
    "2. 환불 안내\n환불 보일러플레이트.\n"
    "3. 상세내용\n마루공원 테니스장 일반 이용 안내입니다.\n"
    "4. 주의사항\n폭염 특보 발효 시 야외 코트 이용을 제한하며, 오후 시간대 운영을 단축합니다."
)


def _op_detail_intake():
    return make_intake(
        turn_kind=TurnKind.NEW,
        action=IntakeAction.OUT_OF_SCOPE,
        oos_type="operational_detail",
        user_rationale="운영 상세 질문 — 시설 식별이 필요합니다.",
    )


def _fake_reader_with_detail(detail: str | None) -> OnDataReader:
    """hydrate/map 은 기존 default 동작을 패치로 두고, fetch_detail_content 만 주입."""
    reader = OnDataReader.__new__(OnDataReader)
    reader.fetch_detail_content = AsyncMock(return_value=detail)  # type: ignore[attr-defined]
    return reader


async def test_162_163_reproduction_reaches_excerpt_answer():
    intake = _op_detail_intake()
    vrows = [
        {"service_id": "V1", "service_name": "마루공원 테니스장", "similarity": 0.9}
    ]
    hydrated = [
        {
            "service_id": "V1",
            "service_name": "마루공원 테니스장",
            "place_name": "마루공원",
            "area_name": "강남구",
            "service_status": "접수중",
            "service_url": "https://yeyak.seoul.go.kr/v1",
        }
    ]

    async def _search(state, *args, **kwargs):
        return {"plan": {"refined_query": "마루공원 테니스장 폭염철 이용안내"}, "vector": {"results": vrows}}

    async def _hydrate(session, ids):
        return hydrated if ids else []

    # 실제 AnswerAgent + fake LLM 으로 운영-상세 프롬프트 선택을 단언.
    mock_model = MagicMock()
    mock_model.__or__ = MagicMock(return_value=MagicMock())
    mock_model.with_structured_output = MagicMock(return_value=MagicMock())
    answer_agent = AnswerAgent(model=mock_model)
    answer_agent._answer_chain = MagicMock()
    answer_agent._answer_chain.ainvoke = AsyncMock(
        return_value="폭염 특보 시 야외 코트 이용이 제한되고 오후 운영이 단축됩니다."
    )

    reader = _fake_reader_with_detail(_DETAIL)
    router = make_router(IntentType.VECTOR_SEARCH)

    with patch(
        "agents.vector_agent.VectorAgent.search", AsyncMock(side_effect=_search)
    ), patch(
        "agents.hydration_node.hydrate_services", AsyncMock(side_effect=_hydrate)
    ):
        graph = AgentGraph(intake=intake, router=router, answer_agent=answer_agent)
        # operational_detail fetch 가 OnDataReader.fetch_detail_content 경유하도록 주입.
        graph._nodes._retrieval._ondata = reader
        result = await run_graph(
            graph,
            make_agent_state(message="마루공원 테니스장 폭염철 이용안내에 대해 알려줘"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )

    # focal service_id 로 detail_content fetch.
    reader.fetch_detail_content.assert_awaited_once_with("V1")

    # answer 가 운영-상세 발췌 프롬프트를 선택했고, 섹션4 폭염 발췌가 컨텍스트에 전달됨.
    last = answer_agent._answer_chain.ainvoke.call_args[0][0]
    assert _STRUCT_OPERATIONAL_DETAIL[:30] in last["system"]
    assert "폭염 특보 발효 시 야외 코트 이용을 제한" in str(last)
    # 거짓 단정/도메인 거절 미발생 — 실제 발췌 기반 안내 도달.
    assert "폭염" in result["output"]["answer"]


async def test_no_heat_text_falls_back_honestly():
    """detail_content 에 폭염 텍스트 부재 → 발췌 None → interim 정직 폴백."""
    intake = _op_detail_intake()
    vrows = [{"service_id": "V1", "service_name": "마루공원 테니스장", "similarity": 0.9}]
    hydrated = [
        {
            "service_id": "V1",
            "service_name": "마루공원 테니스장",
            "place_name": "마루공원",
            "service_url": "https://yeyak.seoul.go.kr/v1",
        }
    ]

    async def _search(state, *args, **kwargs):
        return {"plan": {"refined_query": "마루공원 폭염"}, "vector": {"results": vrows}}

    async def _hydrate(session, ids):
        return hydrated if ids else []

    from tests.helpers import make_answer_agent

    answer_agent = make_answer_agent("공식 페이지에서 정확한 운영 안내를 확인해 주세요.")
    reader = _fake_reader_with_detail(
        "3. 상세내용\n주차와 환불 안내만 담겨 있습니다. 추가 설명이 이어집니다."
    )
    router = make_router(IntentType.VECTOR_SEARCH)

    with patch(
        "agents.vector_agent.VectorAgent.search", AsyncMock(side_effect=_search)
    ), patch(
        "agents.hydration_node.hydrate_services", AsyncMock(side_effect=_hydrate)
    ):
        graph = AgentGraph(intake=intake, router=router, answer_agent=answer_agent)
        graph._nodes._retrieval._ondata = reader
        result = await run_graph(
            graph,
            make_agent_state(message="마루공원 테니스장 폭염철 이용안내 알려줘"),
            data_session=MagicMock(),
            ai_session=make_ai_session(),
        )

    from agents.answer_agent import _STRUCT_ATTRIBUTE_GAP

    last_system = answer_agent._answer_chain.ainvoke.call_args[0][0]["system"]
    assert _STRUCT_ATTRIBUTE_GAP[:30] in last_system
    assert result["output"]["answer"]
