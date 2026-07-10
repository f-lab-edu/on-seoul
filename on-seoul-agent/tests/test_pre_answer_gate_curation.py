"""pre_answer_gate_node 카드 큐레이션 합류.

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
        # post-RRF 게이트가 area 불일치(서초구)를 제거하므로 EXACT(광진구)만 남는다.
        # 게이트가 행을 제거했으므로 hydration 슬롯도 교정 결과로 재기록된다.
        assert [r["service_id"] for r in display] == ["EXACT"]
        assert out["curated_extra_count"] == 0
        assert out["curated_alt_count"] == 0  # 대안(서초구)은 게이트에서 제거됨
        assert out["hydration"]["hydrated_services"] == [
            r for r in rows if r["service_id"] == "EXACT"
        ]

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


class TestStructuredGateInPreAnswer:
    """post-RRF 게이트가 hydration 슬롯을 교정하는지 확인."""

    async def test_gate_removes_leaked_district_and_rewrites_hydration(self):
        """행23: area 활성 시 타지역 누출 행이 게이트에서 하드 제거되고
        hydration 슬롯이 축소셋으로 재기록된다(alt 카드 승격 아님)."""
        nodes = _make_nodes()
        rows = [
            _row("KEEP", area="강서구", klass="체육시설"),
            _row("LEAK", area="강동구", klass="체육시설"),
        ]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name=["강서구"],
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert "hydration" in out
        kept = out["hydration"]["hydrated_services"]
        assert [r["service_id"] for r in kept] == ["KEEP"]
        # 강동구는 alt 카드로도 남지 않는다(active area = 하드 제거).
        assert all(r["service_id"] != "LEAK" for r in out["curated_display"])

    async def test_gate_emptying_all_rows_rewrites_empty_slot(self):
        """Fail-safe 진입점(anchor 7): 게이트가 전부 비우면 hydration 슬롯을
        []로 재기록 → route_pre_answer_gate 가 0건 게이트로 retry 를 태운다."""
        nodes = _make_nodes()
        rows = [_row("LEAK", area="강동구", klass="체육시설")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name=["강서구"],
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["hydration"] == {"hydrated_services": []}
        # 0건이므로 큐레이션 스킵.
        assert out["curated_display"] is None

    async def test_no_active_filter_leaves_hydration_untouched(self):
        """필터 미적용이면 게이트 no-op — hydration 슬롯을 건드리지 않는다."""
        nodes = _make_nodes()
        rows = [_row("A", area="강동구", klass="체육시설")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert "hydration" not in out

    async def test_audience_gate_drops_conflicting_target_in_pre_answer(self):
        """행20/25/30: 대상 명시 시 상충 대상 행이 게이트에서 제거된다."""
        nodes = _make_nodes()
        rows = [
            {"service_id": "KID", "service_name": "KID", "area_name": "강서구",
             "target_info": "초등학생", "max_class_name": "교육"},
            {"service_id": "ADULT", "service_name": "ADULT", "area_name": "강서구",
             "target_info": "성인", "max_class_name": "교육"},
        ]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            target_audience="CHILD",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        kept = out["hydration"]["hydrated_services"]
        assert [r["service_id"] for r in kept] == ["KID"]
