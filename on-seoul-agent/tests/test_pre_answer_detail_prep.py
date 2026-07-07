"""pre_answer prep — operational_detail focal fetch + 발췌 적재 테스트.

pre_answer_gate_node 가 operational_detail turn 에서 focal service_id 의
detail_content 를 fetch 하고 prepare_detail_excerpt 로 발췌해 state detail_excerpt
슬롯에 적재한다. fetch 는 OnDataReader 게이트웨이 경유(가짜 reader 주입).
"""

from unittest.mock import AsyncMock

from agents._ondata_gateway import OnDataReader
from agents.analytics_agent import AnalyticsAgent
from agents.hydration_node import HydrationNode
from agents.nodes.retrieval import RetrievalNodes
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from schemas.state import ActionType, IntentType
from tests.helpers import make_agent_state


def _make_retrieval(reader: OnDataReader) -> RetrievalNodes:
    return RetrievalNodes(
        sql=SqlAgent.__new__(SqlAgent),
        vector=VectorAgent.__new__(VectorAgent),
        analytics=AnalyticsAgent.__new__(AnalyticsAgent),
        hydration=HydrationNode.__new__(HydrationNode),
        ondata=reader,
    )


def _fake_reader(detail: str | None) -> OnDataReader:
    reader = OnDataReader.__new__(OnDataReader)
    reader.fetch_detail_content = AsyncMock(return_value=detail)  # type: ignore[attr-defined]
    return reader


def _op_state(rows, **kw):
    return make_agent_state(
        action=ActionType.OUT_OF_SCOPE,
        out_of_scope_type="operational_detail",
        intent=IntentType.VECTOR_SEARCH,
        vector_sub_intent="operational_detail",
        hydrated_services=rows,
        **kw,
    )


async def test_prep_loads_excerpt_for_operational_detail():
    detail = (
        "3. 상세내용\n시설 일반 안내입니다.\n"
        "4. 주의사항\n폭염 특보 발효 시 야외 활동을 제한하고 운영을 단축합니다."
    )
    reader = _fake_reader(detail)
    nodes = _make_retrieval(reader)
    rows = [{"service_id": "A1", "place_name": "마루공원"}]
    state = _op_state(rows, message="마루공원 폭염철 이용안내")

    update = await nodes.pre_answer_gate_node(state)

    # focal(첫 결과) service_id 로 fetch 했다.
    reader.fetch_detail_content.assert_awaited_once_with("A1")
    assert update["detail_excerpt"] is not None
    assert "폭염" in update["detail_excerpt"]


async def test_prep_excerpt_none_when_keyword_absent():
    detail = "3. 상세내용\n주차와 환불 안내만 담겨 있습니다. 추가 설명이 이어집니다."
    reader = _fake_reader(detail)
    nodes = _make_retrieval(reader)
    rows = [{"service_id": "A1", "place_name": "마루공원"}]
    state = _op_state(rows, message="마루공원 폭염철 이용안내")

    update = await nodes.pre_answer_gate_node(state)
    # 질의 키워드(폭염) 본문 부재 → 발췌 None(정직 폴백 신호).
    assert update["detail_excerpt"] is None


async def test_prep_skipped_for_non_operational_detail():
    """attribute_gap 등 비-operational_detail 은 fetch 하지 않는다(블롭 격리)."""
    reader = _fake_reader("3. 상세내용\n폭염 안내")
    nodes = _make_retrieval(reader)
    rows = [{"service_id": "A1", "place_name": "마루공원"}]
    state = make_agent_state(
        action=ActionType.OUT_OF_SCOPE,
        out_of_scope_type="attribute_gap",
        intent=IntentType.VECTOR_SEARCH,
        vector_sub_intent="attribute_gap",
        hydrated_services=rows,
        message="마루공원 보수공사 일정",
    )

    update = await nodes.pre_answer_gate_node(state)
    reader.fetch_detail_content.assert_not_called()
    assert update.get("detail_excerpt") is None


async def test_prep_no_focal_no_fetch():
    """0건(focal 없음)이면 fetch 하지 않는다."""
    reader = _fake_reader("폭염")
    nodes = _make_retrieval(reader)
    state = _op_state([], message="폭염철 이용안내")

    update = await nodes.pre_answer_gate_node(state)
    reader.fetch_detail_content.assert_not_called()
    assert update.get("detail_excerpt") is None


async def test_prep_fetch_exception_isolated():
    """fetch 예외는 best-effort 격리 — detail_excerpt=None, 답변 막지 않음."""
    reader = OnDataReader.__new__(OnDataReader)
    reader.fetch_detail_content = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[attr-defined]
    nodes = _make_retrieval(reader)
    rows = [{"service_id": "A1", "place_name": "마루공원"}]
    state = _op_state(rows, message="마루공원 폭염철 이용안내")

    update = await nodes.pre_answer_gate_node(state)
    assert update.get("detail_excerpt") is None
