"""B-2 — pre_answer_gate_node 카드 큐레이션 합류.

카드형 턴(SQL/VECTOR 비-identification)에서만 _curate_display 로 display/extra_count/
alt_count 를 상태에 적재하고, result_quality 를 *큐레이션된* display 기준으로 산출한다.
상세형/attribute_gap/operational/MAP/ANALYTICS 는 비대상(슬롯 None 유지).
"""

from unittest.mock import MagicMock

from agents.nodes.retrieval import RetrievalNodes
from schemas.state import ActionType, IntentType
from tests.helpers import make_agent_state


def _make_nodes() -> RetrievalNodes:
    return RetrievalNodes(
        sql=MagicMock(),
        vector=MagicMock(),
        analytics=MagicMock(),
        hydration=MagicMock(),
        ondata=MagicMock(),
    )


def _row(sid, *, area=None, klass=None, pay=None, status=None):
    return {
        "service_id": sid,
        "service_name": sid,
        "area_name": area,
        "max_class_name": klass,
        "payment_type": pay,
        "service_status": status,
    }


class TestCardTurnCuration:
    async def test_sql_card_turn_curates_and_loads_slots(self):
        nodes = _make_nodes()
        rows = [
            _row("OTHER", area="서초구", klass="체육시설"),
            _row("EXACT", area="광진구", klass="체육시설"),
        ]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            max_class_name="체육시설",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        display = out["curated_display"]
        # 광진구+체육시설(딱맞음)이 상단, 서초구는 강등.
        assert display[0]["service_id"] == "EXACT"
        assert out["curated_extra_count"] == 0
        assert out["curated_alt_count"] == 1  # 서초구 1건 대안

    async def test_extra_count_is_curated_remainder(self):
        nodes = _make_nodes()
        rows = [_row(f"P{i}", area="광진구") for i in range(7)]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert len(out["curated_display"]) == 5
        assert out["curated_extra_count"] == 2

    async def test_result_quality_uses_curated_display(self):
        """빈약(thin) 판정이 큐레이션된 display 기준(≤2)으로 산출된다."""
        nodes = _make_nodes()
        rows = [_row("A", area="광진구"), _row("B", area="광진구")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"]["thin"] is True

    async def test_intended_restored_from_relaxed_values(self):
        """완화로 filters 가 비어도 relaxed_values 로 의도 제약을 복원해 정렬한다."""
        nodes = _make_nodes()
        rows = [
            _row("SEOCHO", area="서초구", klass="체육시설"),
            _row("GWANGJIN", area="광진구", klass="문화행사"),
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.RETRIEVE,
            hydrated_services=rows,
            retry_relaxed=True,
            relaxed_filters=["area_name", "payment_type"],
            relaxed_values={"area_name": "광진구", "payment_type": "무료"},
        )
        out = await nodes.pre_answer_gate_node(state)
        # 광진구(area 복원 매칭)가 서초구보다 상단.
        assert out["curated_display"][0]["service_id"] == "GWANGJIN"


class TestNonCardTurnsSkipCuration:
    async def test_attribute_gap_not_curated(self):
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            vector_sub_intent="attribute_gap",
            hydrated_services=[_row("A", area="광진구")],
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["curated_display"] is None

    async def test_identification_detail_not_curated(self):
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.RETRIEVE,
            vector_sub_intent="identification",
            hydrated_services=[_row("A", area="광진구")],
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["curated_display"] is None

    async def test_map_not_curated(self):
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.MAP,
            action=ActionType.RETRIEVE,
            hydrated_services=[_row("A", area="광진구")],
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["curated_display"] is None

    async def test_zero_hits_no_curation(self):
        """0건은 큐레이션 대상 아님(0건 게이트가 retry 로 보냄)."""
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            hydrated_services=[],
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["curated_display"] is None
