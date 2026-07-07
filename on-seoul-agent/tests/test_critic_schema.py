"""schemas/critic.py 단위 테스트 (L1 retrieval-critic Phase 1 — 스키마 스캐폴딩).

CriticOutput 구조화 출력 스키마를 검증한다:
  - decision 3택 enum (ANSWER/REPLAN/STOP)
  - replan_hint 화이트리스트 제약 (인젝션 가드): 자유 SQL/컬럼/식별자 표현 불가
  - with_structured_output 호환 파싱 (dict → 모델)

이 단계는 스캐폴딩이므로 critic 노드·그래프 배선은 없다. 스키마만 검증한다.
"""

import pytest
from pydantic import ValidationError

from schemas.critic import (
    ALLOWED_DROP_FILTERS,
    CriticDecision,
    CriticOutput,
    ReplanHint,
)
from schemas.state import IntentType


class TestCriticDecision:
    def test_three_choices(self):
        assert {d.value for d in CriticDecision} == {"ANSWER", "REPLAN", "STOP"}

    def test_answer(self):
        out = CriticOutput(decision="ANSWER", rationale="결과가 충분합니다.")
        assert out.decision == CriticDecision.ANSWER

    def test_stop(self):
        out = CriticOutput(decision="STOP", rationale="더 나은 결과가 없습니다.")
        assert out.decision == CriticDecision.STOP

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            CriticOutput(decision="RETRY", rationale="x")


class TestReplanHintWhitelist:
    """인젝션 가드: replan_hint 는 IntentType enum + 화이트리스트 필터명으로만 제약."""

    def test_intent_is_enum(self):
        hint = ReplanHint(intent="VECTOR_SEARCH", reason="정형 검색이 약함")
        assert hint.intent == IntentType.VECTOR_SEARCH

    def test_invalid_intent_rejected(self):
        with pytest.raises(ValidationError):
            ReplanHint(intent="DROP TABLE", reason="x")

    def test_drop_filters_whitelist_ok(self):
        hint = ReplanHint(drop_filters=["area_name", "service_status"], reason="완화")
        assert hint.drop_filters == ["area_name", "service_status"]

    def test_target_audience_is_droppable(self):
        # P1+P2: target_audience 도 critic/retry 가 완화할 수 있어야 한다.
        assert "target_audience" in ALLOWED_DROP_FILTERS
        hint = ReplanHint(drop_filters=["target_audience"], reason="대상 완화")
        assert hint.drop_filters == ["target_audience"]

    def test_drop_filters_rejects_free_identifier(self):
        # 자유 컬럼/식별자(화이트리스트 밖)는 거부 — SQL 인젝션 가드.
        with pytest.raises(ValidationError):
            ReplanHint(drop_filters=["password; DROP TABLE users"], reason="x")

    def test_drop_filters_rejects_unknown_column(self):
        with pytest.raises(ValidationError):
            ReplanHint(drop_filters=["created_at"], reason="x")

    def test_reformulate_query_is_plain_string(self):
        hint = ReplanHint(reformulate_query="실내 수영장", reason="자연어 재구성")
        assert hint.reformulate_query == "실내 수영장"

    def test_reason_required(self):
        with pytest.raises(ValidationError):
            ReplanHint(intent="VECTOR_SEARCH")

    def test_whitelist_matches_filter_state_keys(self):
        # 화이트리스트가 FilterState 키(post-filter)와 일치하는지 — 계약 정합.
        from schemas.state import FilterState

        assert set(ALLOWED_DROP_FILTERS) == set(FilterState.__annotations__.keys())


class TestCriticOutputParsing:
    """with_structured_output 은 LLM JSON 을 dict 로 넘겨 model_validate 한다."""

    def test_replan_with_hint_parses(self):
        raw = {
            "decision": "REPLAN",
            "replan_hint": {
                "intent": "VECTOR_SEARCH",
                "drop_filters": ["area_name"],
                "reformulate_query": "무료 실내 수영장",
                "reason": "정형 필터가 과했음",
            },
            "rationale": "지역 필터를 완화해 벡터 검색으로 다시 찾습니다.",
        }
        out = CriticOutput.model_validate(raw)
        assert out.decision == CriticDecision.REPLAN
        assert out.replan_hint is not None
        assert out.replan_hint.intent == IntentType.VECTOR_SEARCH
        assert out.replan_hint.drop_filters == ["area_name"]

    def test_answer_without_hint(self):
        out = CriticOutput.model_validate(
            {"decision": "ANSWER", "rationale": "충분함"}
        )
        assert out.replan_hint is None

    def test_replan_hint_free_column_rejected_on_parse(self):
        raw = {
            "decision": "REPLAN",
            "replan_hint": {
                "drop_filters": ["1=1 OR service_status"],
                "reason": "x",
            },
            "rationale": "y",
        }
        with pytest.raises(ValidationError):
            CriticOutput.model_validate(raw)
