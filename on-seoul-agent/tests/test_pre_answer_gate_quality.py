"""P2 자각 패스(B) — pre_answer_gate_node 가 result_quality/reservation_guide_shown
부분 dict 를 산출하는지 검증한다.

RETRIEVE(hydration 결과) 경로에서만 평가하고, best-effort 격리(점검 예외가 답변을
막지 않음)와 라우팅 불변(저품질은 retry 가 아니라 answer 로 전진)을 가드한다.
"""

from unittest.mock import MagicMock

import pytest

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


def _retrieve_state(rows, **overrides):
    return make_agent_state(
        intent=IntentType.SQL_SEARCH,
        action=ActionType.RETRIEVE,
        hydrated_services=rows,
        **overrides,
    )


def _rows(areas):
    return [{"service_id": f"P{i}", "area_name": a} for i, a in enumerate(areas)]


class TestPreAnswerGateResultQuality:
    async def test_skew_sets_result_quality(self):
        """5/5 강남(지역 미지정) → result_quality.skew 세팅."""
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5))
        out = await nodes.pre_answer_gate_node(state)

        rq = out["result_quality"]
        assert rq is not None
        assert rq["skew_field"] == "area_name"
        assert rq["skew_value"] == "강남구"

    async def test_mixed_results_no_quality_flag(self):
        """혼합 결과 → result_quality=None (현행 조립 그대로)."""
        nodes = _make_nodes()
        state = _retrieve_state(
            _rows(["강남구", "마포구", "송파구", "종로구", "강서구"])
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is None

    async def test_area_filter_suppresses_skew(self):
        """사용자가 area_name 명시(applied_filters) → 쏠림 억제."""
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5), area_name="강남구")
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is None

    async def test_thin_sets_flag(self):
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구", "마포구"]))
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"]["thin"] is True

    async def test_non_retrieve_action_not_evaluated(self):
        """attribute_gap/describe 등 비-RETRIEVE 는 평가 대상 아님 → None."""
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            action=ActionType.OUT_OF_SCOPE,
            out_of_scope_type="attribute_gap",
            hydrated_services=_rows(["강남구"] * 5),
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is None

    @pytest.mark.parametrize(
        "action",
        [
            ActionType.DIRECT_ANSWER,
            ActionType.EXPLAIN,
            ActionType.AMBIGUOUS,
            ActionType.OUT_OF_SCOPE,
        ],
    )
    async def test_all_non_retrieve_actions_skip_quality(self, action):
        """비-RETRIEVE action 전수 — describe/MAP/ANALYTICS/clarify 류는 None 유지."""
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=action,
            hydrated_services=_rows(["강남구"] * 5),
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is None
        # 비-RETRIEVE 라도 reservation 신호는 굳이 막지 않으나 history 없으면 False.
        assert out["reservation_guide_shown"] is False

    async def test_action_none_router_fallback_evaluated(self):
        """action=None(router fallback, 검색 실행)은 RETRIEVE 와 동일 취급 → 평가함."""
        nodes = _make_nodes()
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            action=None,
            hydrated_services=_rows(["강남구"] * 5),
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is not None
        assert out["result_quality"]["skew_field"] == "area_name"

    async def test_reservation_guide_shown_from_history(self):
        """직전 assistant 발화에 통합회원 안내가 있으면 reservation_guide_shown=True."""
        nodes = _make_nodes()
        state = _retrieve_state(
            _rows(["강남구"] * 5),
            history=[
                {"role": "user", "content": "강남구 수영장"},
                {
                    "role": "assistant",
                    "content": "수영장입니다. 서울시 통합회원 가입이 필요합니다.",
                },
            ],
        )
        out = await nodes.pre_answer_gate_node(state)
        assert out["reservation_guide_shown"] is True

    async def test_reservation_guide_not_shown_empty_history(self):
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5))
        out = await nodes.pre_answer_gate_node(state)
        assert out["reservation_guide_shown"] is False

    async def test_node_path_recorded(self):
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5))
        out = await nodes.pre_answer_gate_node(state)
        assert out["node_path"] == ["pre_answer_gate"]

    async def test_assess_exception_isolated(self, monkeypatch):
        """점검 예외 → result_quality=None(best-effort, 답변 막지 않음)."""
        import agents.nodes.retrieval as retrieval_mod

        def _boom(*a, **k):
            raise RuntimeError("heuristic blew up")

        monkeypatch.setattr(retrieval_mod, "assess_result_quality", _boom)
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5))
        out = await nodes.pre_answer_gate_node(state)
        assert out["result_quality"] is None
        assert out["node_path"] == ["pre_answer_gate"]


class TestPreAnswerGateRoutingUnchanged:
    """라우팅 불변: 저품질(쏠림/빈약)은 retry 가 아니라 answer 로 전진."""

    def test_skew_routes_to_answer(self):
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"] * 5))
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_thin_routes_to_answer(self):
        nodes = _make_nodes()
        state = _retrieve_state(_rows(["강남구"]))
        assert nodes.route_pre_answer_gate(state) == "answer_node"

    def test_zero_hits_still_routes_retry(self):
        """0건 분기는 유지(이 PR 불변)."""
        nodes = _make_nodes()
        state = _retrieve_state([])
        assert nodes.route_pre_answer_gate(state) == "retry_prep_node"
