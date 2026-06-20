"""RRF 융합 토폴로지 가드 테스트 (조용한 오작동 차단).

배경 — 확인된 결함:
    agents/graph.py 엣지: `hydration_node → rrf_fusion_node → pre_answer_gate_node`.
    fusion 이 hydration **뒤**라, rrf_fusion_node 가 만드는 rrf_merged_ids 를
    hydration_node(state["rrf_merged_ids"] 를 읽어 hydrate)가 소비하지 못한다.
    enable_secondary_intent=True 활성화 시 fan-out RRF 결과가 조용히 버려진다.
    현재 flag off 라 rrf_fusion_node 는 내부 early-return no-op 이므로 동작은 안전.

이 파일의 두 가드는 "사람 기억"에 의존하지 않고 활성화/토폴로지 변경 시 RED 를 강제한다:

(a) 행동 가드(xfail strict) — TestRRFFusionTopologyGuard
    enable_secondary_intent=True 강제 + SQL/VECTOR 둘 다 결과 있는 fan-out 시나리오를
    **실제 배선 순서(hydration → rrf_fusion)** 로 구동해, hydration 이 fusion 의
    rrf_merged_ids 를 소비했는지 단언. 현재 토폴로지에선 fusion 이 hydration 뒤라 실패
    → xfail(strict)로 박제. 토폴로지를 고쳐 fusion 을 hydration 앞으로 옮기면 이 단언이
    통과(xpass) → strict 모드가 FAIL 로 바꿔 "xfail 제거 + 수정 확인"을 강제한다.

    "수정 시 RED" 개념 확인: 아래 _run_in_edge_order 의 호출 순서를
    [fusion → hydration] 으로 바꾸면(= 목표 토폴로지를 가상으로 적용) 단언이 통과해
    xpass 가 되고, strict xfail 이 FAIL 을 발생시킨다. QA 는 이 순서 스왑으로 가드의
    민감도를 확인할 수 있다.

(b) 기본값 tripwire — TestSecondaryIntentDefaultGuard
    settings.enable_secondary_intent 기본값이 False 임을 단언. 소스 기본값을 True 로
    바꾸면 FAIL. 활성화 전 RRF 토폴로지 수정이 선결과제임을 박제한다.

⚠️ 한계: (b)는 소스 기본값만 방어한다. 런타임 env 플립
   (ON_SEOUL_ENABLE_SECONDARY_INTENT=true 등)까지는 막지 못한다 — 그 경우 (a)가
   행동 레벨에서 결함을 드러내는 1차 방어선이다.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.graph import AgentGraph
from agents.nodes import GraphNodes
from core.config import Settings, settings
from schemas.state import AgentState, IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _state(**kwargs: Any) -> AgentState:
    return make_agent_state(**kwargs)


def _nodes() -> GraphNodes:
    return AgentGraph(answer_agent=make_answer_agent())._nodes


async def _run_in_edge_order(
    nodes: GraphNodes,
    state: AgentState,
    *,
    hydrated_by_id: dict[str, dict],
) -> list[str]:
    """실제 컴파일 배선 순서대로 hydration_node → rrf_fusion_node 를 구동한다.

    graph.py 엣지(`hydration_node → rrf_fusion_node`)와 동일한 순서로 두 노드를 직접
    실행하고, 각 노드 update 를 state 에 머지해 다음 노드로 흘린다(LangGraph reducer 의
    얕은 머지를 모사). hydrate_services 는 id→원본 dict 매핑으로 mock 한다.

    Returns:
        hydration 이 최종적으로 채운 hydrated_services 의 service_id 순서 리스트.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield MagicMock()

    async def _fake_hydrate(_session: Any, ids: list[str]) -> list[dict]:
        return [hydrated_by_id[i] for i in ids if i in hydrated_by_id]

    with (
        patch("agents.nodes.data_session_ctx", _ctx),
        patch("agents.hydration_node.hydrate_services", AsyncMock(side_effect=_fake_hydrate)),
    ):
        # ── 실제 배선 순서: hydration 먼저, fusion 나중 ──
        hyd_update = await nodes.hydration_node(state)
        _merge(state, hyd_update)

        fus_update = await nodes.rrf_fusion_node(state)
        _merge(state, fus_update)

    hydrated = (state["hydration"].get("hydrated_services") or [])
    return [r["service_id"] for r in hydrated]


def _merge(state: AgentState, update: dict[str, Any]) -> None:
    """노드 update 를 state 에 얕게 머지(중첩 채널은 dict update)."""
    for key, value in update.items():
        if key == "node_path":
            continue
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key].update(value)  # type: ignore[literal-required]
        else:
            state[key] = value  # type: ignore[literal-required]


# ---------------------------------------------------------------------------
# (a) 행동 가드 — xfail strict
# ---------------------------------------------------------------------------


class TestRRFFusionTopologyGuard:
    @pytest.mark.xfail(
        strict=True,
        reason=(
            "RRF fusion이 hydration 뒤(graph.py: hydration_node → rrf_fusion_node) — "
            "fusion 출력(rrf_merged_ids)을 hydration이 소비 못 함. 활성화 전 fusion을 "
            "hydration 앞으로 이동. 토폴로지 수정 후 이 xfail 제거."
        ),
    )
    async def test_hydration_consumes_rrf_merged_ids_in_fanout(self):
        """fan-out 시 hydration 이 fusion 의 rrf_merged_ids 를 소비하는가.

        시나리오: SQL=[S1, S2], VECTOR=[V1, S1]. RRF 는 S1(양 채널 등장)을 1위로 올린다.
        primary=SQL_SEARCH 이므로 fusion 미반영 시 hydration 은 sql.results 를 그대로
        통과시켜 [S1, S2] 순서가 된다. 토폴로지가 올바르면(fusion → hydration) hydration 은
        rrf_merged_ids 를 받아 [S1, V1, ...] 등 RRF 랭킹 순(첫 원소 S1, V1 포함)을 만든다.

        단언: hydration 결과가 RRF 융합을 반영(VECTOR 채널 전용 V1 이 포함)한다.
        현재 배선(hydration 먼저)에선 V1 이 누락되므로 실패 → xfail(strict).
        """
        nodes = _nodes()
        state = _state(
            intent=IntentType.SQL_SEARCH,
            secondary_intent=IntentType.VECTOR_SEARCH,
            sql_results=[{"service_id": "S1"}, {"service_id": "S2"}],
            vector_results=[{"service_id": "V1"}, {"service_id": "S1"}],
        )
        hydrated_by_id = {
            "S1": {"service_id": "S1", "service_name": "수영장"},
            "S2": {"service_id": "S2", "service_name": "도서관"},
            "V1": {"service_id": "V1", "service_name": "체험관"},
        }

        with patch("agents.nodes.settings") as mock_settings:
            mock_settings.enable_secondary_intent = True
            mock_settings.rrf_k_constant = 60
            mock_settings.rrf_top_k_final = 10
            order = await _run_in_edge_order(
                nodes, state, hydrated_by_id=hydrated_by_id
            )

        # RRF 융합이 반영됐다면 VECTOR 전용 결과 V1 이 hydration 결과에 포함돼야 한다.
        # 현재 토폴로지(fusion 이 hydration 뒤)에선 hydration 이 sql.results 만 통과시켜
        # V1 이 누락된다 → 이 단언 실패(xfail). fusion 을 hydration 앞으로 옮기면 통과(xpass).
        assert "V1" in order, (
            f"hydration이 RRF 융합 결과를 소비하지 못함 — V1 누락. order={order}. "
            "fusion을 hydration 앞으로 이동하면 통과한다."
        )


# ---------------------------------------------------------------------------
# (b) 기본값 tripwire
# ---------------------------------------------------------------------------


class TestSecondaryIntentDefaultGuard:
    def test_enable_secondary_intent_default_is_false(self):
        """settings.enable_secondary_intent 기본값이 False 인지 단언(소스 기본값 방어).

        활성화 전 RRF 토폴로지 수정 필요 — graph.py 엣지부 TODO 참조. 켜려면 이 가드 제거 +
        fusion 을 hydration 앞으로 이동(test_hydration_consumes_rrf_merged_ids_in_fanout
        xfail 도 함께 제거)할 것. 소스 기본값을 True 로 바꾸면 이 테스트가 FAIL 한다.

        한계: 런타임 env 플립(ON_SEOUL_ENABLE_SECONDARY_INTENT=true)은 막지 못한다.
        그 경우 (a) 행동 가드가 1차 방어선이다.
        """
        # 모델 클래스 기본값(런타임 env override 무관) 단언.
        assert Settings.model_fields["enable_secondary_intent"].default is False
        # 현재 로드된 settings 인스턴스도 기본 비활성 상태인지 보조 확인.
        assert settings.enable_secondary_intent is False
