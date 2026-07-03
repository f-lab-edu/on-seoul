"""AgentState critic 슬롯 + 단일 retrieval 예산 스캐폴딩 테스트 (Phase 1).

이 단계는 스캐폴딩이라 슬롯을 소비하는 노드가 아직 없다. 슬롯 존재 + 예산
초기화(_prepare_state)만 검증한다. 기존 그래프 회귀 0(별도 스위트).
"""

from agents.graph import _prepare_state
from core.config import Settings
from schemas.state import AgentState


class TestCriticStateSlots:
    def test_critic_slots_present_in_annotations(self):
        ann = AgentState.__annotations__
        assert "critic_decision" in ann
        assert "critic_replan_hint" in ann
        assert "critic_rationale" in ann


class TestBudgetInit:
    def test_prepare_state_defaults_retry_count_zero(self):
        # 부분 dict(retry_count 미포함) → 0 으로 초기화(기존 동작 불변).
        state: AgentState = {}  # type: ignore[typeddict-item]
        out = _prepare_state(state)
        assert out["retry_count"] == 0

    def test_prepare_state_preserves_existing_retry_count(self):
        state: AgentState = {"retry_count": 1}  # type: ignore[typeddict-item]
        out = _prepare_state(state)
        assert out["retry_count"] == 1


class TestBudgetConfig:
    def test_single_budget_cap_default_two(self):
        # 단일 retrieval 예산 캡 = 기본 2회. self_correction/critic 공유 단일 출처.
        s = Settings.model_construct()
        assert s.max_retrieval_retries == 2
