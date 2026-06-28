"""QA 보완 — 카드 큐레이션 빈틈/회귀 가드.

본 변경(답변 카드 일관성 A + 적합도 큐레이션 B)이 명시 테스트로 덮지 않은 경로를
가드한다:
  · answer 폴백 — curated_display=None(비카드형/예외) 시 기존 슬라이스 경로로 폴백(동작 불변).
  · 큐레이션 예외 best-effort — _curate_display 가 던져도 슬롯 None + result_quality 폴백.
  · _curate_score 화이트리스트 정확비교 — 표기 변형은 매칭 안 됨(R-1 의 정확비교 측면).
  · extra_count 음수 방지.
  · few-exact/many-raw 현행 동작 핀(혼합-A: thin 아님, alt 라벨로 처리) — 회귀 앵커.
  · 동점 stable 보존이 큐레이션 합류 후에도 유지(RRF/SQL 순서).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from agents.answer_agent import (
    _curate_display,
    _curate_score,
    _DISPLAY_LIMIT,
)
from agents.graph import AgentGraph
from agents.nodes.retrieval import RetrievalNodes
from schemas.state import ActionType, IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _nodes() -> RetrievalNodes:
    return RetrievalNodes(
        sql=MagicMock(),
        vector=MagicMock(),
        analytics=MagicMock(),
        hydration=MagicMock(),
        ondata=MagicMock(),
    )


def _row(sid, *, area=None, klass=None, pay=None, status="접수중"):
    return {
        "service_id": sid,
        "service_name": sid,
        "place_name": sid,
        "area_name": area,
        "max_class_name": klass,
        "payment_type": pay,
        "service_status": status,
    }


class TestAnswerFallbackWhenNoCuration:
    """curated_display=None 이면 answer 는 기존 all_results[:5] 슬라이스로 폴백한다(동작 불변)."""

    async def test_fallback_slices_raw_when_slot_none(self):
        agent = make_answer_agent("답변")
        rows = [_row(f"X{i}", area="광진구") for i in range(9)]
        # curated_display 미적재(None) — 폴백 경로.
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            hydrated_services=rows,
            curated_display=None,
        )
        out = await agent.answer(state)
        # 기존 동작: 상위 5건 카드 + "외 4건".
        assert len(out["service_cards"]) == _DISPLAY_LIMIT
        call = agent._answer_chain.ainvoke.call_args[0][0]
        sent = json.loads(call["results_json"])
        assert len(sent) == _DISPLAY_LIMIT
        # 폴백 extra_count = len(all)-5 = 4 (raw 기준 — 큐레이션 없음).
        assert "4" in call["more_notice"]

    async def test_fallback_preserves_raw_order(self):
        """폴백 경로는 큐레이션 정렬을 적용하지 않고 입력 순서를 그대로 슬라이스한다."""
        agent = make_answer_agent("답변")
        rows = [_row("A", area="서초구"), _row("B", area="광진구")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            area_name="광진구",
            hydrated_services=rows,
            curated_display=None,
        )
        out = await agent.answer(state)
        # 큐레이션 미적용 → 광진구로 재정렬되지 않고 입력순(A,B) 그대로.
        assert [c["service_id"] for c in out["service_cards"]] == ["A", "B"]


class TestCurationExceptionBestEffort:
    """pre_answer_gate 큐레이션이 예외를 던져도 답변을 막지 않는다(슬롯 None 폴백)."""

    async def test_curate_exception_yields_none_slots(self):
        nodes = _nodes()
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            hydrated_services=[_row("A", area="광진구")],
        )
        with patch(
            "agents.nodes.retrieval._curate_display",
            side_effect=RuntimeError("boom"),
        ):
            out = await nodes.pre_answer_gate_node(state)
        # best-effort: 예외 흡수 → 모든 큐레이션 슬롯 None + result_quality None.
        assert out["curated_display"] is None
        assert out["curated_extra_count"] is None
        assert out["curated_alt_count"] is None
        assert out["result_quality"] is None


class TestCurateScoreWhitelist:
    """적합도 비교는 화이트리스트 정규값 정확 비교(R-1: 표기 변형은 매칭 안 됨)."""

    def test_variant_string_does_not_match(self):
        intended = {"area_name": "강남구"}
        exact = _curate_score(_row("X", area="강남구"), intended)
        variant = _curate_score(_row("Y", area="강남"), intended)  # 비공식 표기
        # area 차원: 정확일치=0, 변형=1 → 변형이 후순위(값이 큼).
        assert exact[0] == 0
        assert variant[0] == 1

    def test_missing_intended_dim_is_neutral(self):
        """intended 에 없는 차원은 만족(0)으로 둬 정렬에 영향 없음."""
        score = _curate_score(_row("X", area="강남구"), {})
        # area/category/payment 모두 0(중립), 상태만 _STATUS_RANK.
        assert score[:3] == (0, 0, 0)


class TestExtraCountClamp:
    async def test_extra_count_never_negative(self):
        nodes = _nodes()
        rows = [_row("A", area="광진구"), _row("B", area="광진구")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["curated_extra_count"] == 0  # 2건 < 5 → 음수 아님


class TestHybridAFewExactBehaviorPin:
    """혼합-A 핀(회귀 앵커) — 원본 많지만 적합 적을 때.

    혼합-A 는 하드 제외가 없으므로 빈 슬롯을 대안으로 채운다. 따라서:
      · curated_display 는 _DISPLAY_LIMIT 로 채워지고(적합 2건뿐이어도),
      · result_quality.thin 은 display 개수(5)를 보므로 False,
      · 적합 부족은 alt_count(대안 라벨)로 표면화된다.
    "빈약 판정"이 아니라 "대안 라벨"이 few-exact 신호다 — 현행 계약을 핀한다.
    """

    async def test_many_raw_few_exact_uses_alt_label_not_thin(self):
        nodes = _nodes()
        rows = [
            _row("E1", area="광진구", klass="체육시설"),
            _row("E2", area="광진구", klass="체육시설"),
        ] + [_row(f"O{i}", area="서초구", klass="문화행사") for i in range(7)]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            max_class_name="체육시설",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert len(out["curated_display"]) == _DISPLAY_LIMIT
        # 적합 2건만이라도 thin 으로 빠지지 않는다(display=5).
        assert out["result_quality"] is None
        # 적합 부족은 대안 카운트로 표면화(top5: 2 exact + 3 alt).
        assert out["curated_alt_count"] == 3
        # 적합 2건이 상단.
        assert [c["service_id"] for c in out["curated_display"][:2]] == ["E1", "E2"]


class TestStableTieAfterCurationJoin:
    """동점 stable 보존이 pre_answer_gate 큐레이션 합류 후에도 유지(RRF/SQL 순서)."""

    async def test_all_tie_preserves_input_order_through_gate(self):
        nodes = _nodes()
        rows = [_row(s, area="광진구", status="접수중") for s in ("A", "B", "C", "D")]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            hydrated_services=rows,
        )
        out = await nodes.pre_answer_gate_node(state)
        assert [c["service_id"] for c in out["curated_display"]] == ["A", "B", "C", "D"]


class TestRelaxedValuesSnapshotAllPaths:
    """relaxed_values 스냅샷이 완화 경로별로 드롭 직전 원래 값을 보존하는지 가드(5.1).

    M1(gap) 은 test_pre_answer_gate_curation 이, 케이스 C 는 test_non_retrieve_robustness
    가 덮는다. 여기선 케이스 A(intent 전환: SQL→VECTOR)를 덮는다 — 전환 경로도
    완화이므로 의도 복원 스냅샷이 필요하다.
    """

    def _retry_nodes(self):
        return AgentGraph(answer_agent=make_answer_agent())._nodes

    async def test_case_a_intent_switch_snapshots_relaxed_values(self):
        nodes = self._retry_nodes()
        # SQL_SEARCH → VECTOR_SEARCH 전환(케이스 A). filters 는 전체 드롭되지만
        # 원래 값은 relaxed_values 로 복원 가능해야 한다.
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=ActionType.RETRIEVE,
            area_name="광진구",
            payment_type="무료",
            retry_count=0,
        )
        with patch("agents._redis_gateway.release_answer_lock", AsyncMock()):
            update = await nodes.retry_prep_node(state)
        # 전환 경로 — filters 전체 드롭.
        assert update["filters"]["area_name"] is None
        assert update["filters"]["payment_type"] is None
        # 의도 복원용 스냅샷에 드롭 직전 원래 값 보존.
        assert update["relaxed_values"] == {"area_name": "광진구", "payment_type": "무료"}
        assert set(update["relaxed_filters"]) == {"area_name", "payment_type"}


class TestCurateSortReturnsAllNotSliced:
    """_curate_display 는 전체를 반환하고 슬라이스는 호출자 책임(계약 가드)."""

    def test_returns_full_list(self):
        rows = [_row(f"P{i}", area="광진구") for i in range(8)]
        curated, _ = _curate_display(
            rows, {"area_name": "광진구"}, relaxed=False, relaxed_filters=None
        )
        assert len(curated) == 8  # 슬라이스 안 함
