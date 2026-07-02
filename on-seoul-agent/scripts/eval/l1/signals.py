"""질의당 추출 신호 + 버킷 라벨 스키마 — L1 Phase 0 의 단일 계약 출처.

이 모듈의 Pydantic 모델은 파이프라인 전 구간(extract → rule_labeler → llm_classifier
→ aggregate)이 공유하는 **크로스-스테이지 계약**이다. 신호 필드를 바꾸면 라벨러·집계·
픽스처를 같은 커밋에서 함께 갱신한다.

버킷 정의는 계획서 §6 Phase 0 를 따른다:
  - 규칙 자동 라벨(공짜, state 신호): NON_RETRIEVE / ZERO_HIT / THIN / SKEW / RETRIED / NORMAL
  - LLM 판단 라벨: INTENT_MISPICK / DRIFT / COMPOUND_UNEXPRESSIBLE / (판단 불가 시 NORMAL)

분모 스코핑(측정 타당성):
  TriageAgent 가 action≠RETRIEVE(DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN)로
  판정했거나 turn_kind=META(설명·메타)인 턴은 애초에 검색을 시도하지 않았다. 이들은
  NON_RETRIEVE 로 별도 세그먼트되어 L1/L2 수요 분모에서 제외된다 — 그래야 "NORMAL"이
  '검색은 됐고 실패 신호 없음'만 의미하고, 결정 게이트가 정직해진다.

결정 게이트 입력(RETRIEVE 트레이스만 분모):
  - COMPOUND_UNEXPRESSIBLE 비중 = L2 수요
  - 단일-intent 실패(ZERO_HIT/THIN/SKEW/INTENT_MISPICK/DRIFT) = L1 수요
  - turn_kind 세그먼트(DRILL/REFINE 등) = L2(멀티홉/복합) 수요 prior
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RuleBucket(str, Enum):
    """규칙 자동 라벨 — state 신호만으로 결정적 산출(LLM 불필요)."""

    # 검색 미시도 — action≠RETRIEVE 또는 turn_kind=META. L1/L2 분모에서 제외.
    NON_RETRIEVE = "NON_RETRIEVE"
    ZERO_HIT = "ZERO_HIT"  # 결과 0건
    THIN = "THIN"  # 빈약(result_quality.thin)
    SKEW = "SKEW"  # 쏠림(result_quality.skew_*)
    RETRIED = "RETRIED"  # 재시도 발생(retry_count>0 또는 forced_intent)
    NORMAL = "NORMAL"  # 검색은 됐고 실패 신호 없음


class LlmBucket(str, Enum):
    """LLM 판단 라벨 — 규칙으로 결정 불가한 의미적 실패."""

    INTENT_MISPICK = "INTENT_MISPICK"  # intent 오선택
    DRIFT = "DRIFT"  # 질의 의도와 결과 표류
    COMPOUND_UNEXPRESSIBLE = "COMPOUND_UNEXPRESSIBLE"  # 복합 제약 — 단일 intent 표현 불가
    NORMAL = "NORMAL"  # 판단상 정상(의미적 실패 아님)


class QuerySignals(BaseModel):
    """단일 질의(트레이스)에서 추출한 구조화 신호.

    Langfuse 트레이스의 root span metadata + input/output 에서 뽑는다. 프로덕션 트레이스가
    모든 신호를 담지 않을 수 있으므로(§ Langfuse metadata 갭) 미가용 필드는 None 으로 두고
    라벨러가 관대하게(없으면 NORMAL 로 흡수) 처리한다.
    """

    trace_id: str = Field(description="Langfuse 트레이스 ID (동일성 키)")
    raw_query: str = Field(description="사용자 원본 질의")
    intent: str | None = Field(default=None, description="선택된 검색 intent (plan.intent)")
    action: str | None = Field(default=None, description="triage action (ActionType 값)")
    turn_kind: str | None = Field(
        default=None,
        description="triage turn_kind 원본 (TurnKind: NEW/REFINE/DRILL/RELEVANCE/META)",
    )
    sql_hits: int | None = Field(default=None, description="SQL 채널 결과 건수")
    vector_hits: int | None = Field(default=None, description="VECTOR 채널 결과 건수")
    total_hits: int | None = Field(default=None, description="유효 채널 결과 총 건수")
    result_quality: dict | None = Field(
        default=None, description="thin/skew 자각 신호(pre_answer_gate 산출)"
    )
    retry_count: int = Field(default=0, description="self-correction 재시도 횟수")
    forced_intent: str | None = Field(default=None, description="방향성 재시도 강제 intent")
    followup_reask: bool = Field(
        default=False, description="후속 재질문/이탈 신호(세션 내 직후 동일 의도 재질의)"
    )
    # LLM 분류기 입력 보조 — 질의 제약 수 vs router 추출 필터 수 비교용.
    applied_filter_count: int | None = Field(
        default=None, description="router 가 추출·적용한 필터 수"
    )

    def is_meta(self) -> bool:
        """turn_kind=META(설명·메타 턴) — 검색 실패가 아니라 애초에 검색 대상 아님."""
        return self.turn_kind == "META"

    def is_non_retrieve(self) -> bool:
        """검색을 시도하지 않은 턴 — L1/L2 분모에서 제외.

        판정: action 이 RETRIEVE 가 아니거나(DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/
        EXPLAIN) turn_kind 가 META. action 이 None(구 트레이스 — 신호 미가용)이면
        검색 미시도로 단정하지 않는다(하위호환 — turn_kind=META 만으로도 판정).
        """
        if self.is_meta():
            return True
        return self.action is not None and self.action != "RETRIEVE"

    def is_thin(self) -> bool:
        rq = self.result_quality or {}
        return bool(rq.get("thin"))

    def is_skew(self) -> bool:
        rq = self.result_quality or {}
        return rq.get("skew_field") is not None or rq.get("skew_ratio") is not None

    def is_zero_hit(self) -> bool:
        if self.total_hits is not None:
            return self.total_hits == 0
        # total 미가용이면 채널별 합으로 판정, 그것도 없으면 판정 불가(False).
        parts = [h for h in (self.sql_hits, self.vector_hits) if h is not None]
        return bool(parts) and sum(parts) == 0

    def was_retried(self) -> bool:
        return self.retry_count > 0 or self.forced_intent is not None


class LabeledQuery(BaseModel):
    """신호 + 규칙 라벨 + (선택)LLM 라벨 + (선택)사람 라벨 — 검증/집계 단위."""

    signals: QuerySignals
    rule_bucket: RuleBucket
    llm_bucket: LlmBucket | None = None
    llm_rationale: str | None = None
    human_bucket: str | None = Field(
        default=None, description="사람 검증 라벨(rule/llm 버킷 문자열 중 하나)"
    )


class BucketDistribution(BaseModel):
    """최종 버킷 분포 리포트 — 결정 게이트 입력.

    l1_demand / l2_demand 는 계획서 결정 게이트 정의에 따라 파생하며, 분모는
    retrieval_total(실제 검색을 시도한 RETRIEVE 트레이스)이다. NON_RETRIEVE 는
    투명하게 집계되되 수요 계산에서 제외된다.
    판단 자체(L1 계속 vs L2 우선)는 사람 몫 — 여기선 수치만.
    """

    total: int = Field(description="전체 트레이스 수(NON_RETRIEVE 포함)")
    retrieval_total: int = Field(
        description="검색을 실제 시도한 RETRIEVE 트레이스 수(L1/L2 수요 분모)"
    )
    non_retrieve_total: int = Field(
        default=0, description="검색 미시도(action≠RETRIEVE 또는 META) 트레이스 수"
    )
    rule_counts: dict[str, int]
    llm_counts: dict[str, int]
    turn_kind_counts: dict[str, int] = Field(
        default_factory=dict,
        description="RETRIEVE 트레이스의 turn_kind 세그먼트(DRILL/REFINE 등 = L2 prior)",
    )
    l1_demand: int = Field(description="단일-intent 실패 합계(L1 수요, RETRIEVE 분모)")
    l2_demand: int = Field(description="복합-표현불가 합계(L2 수요, RETRIEVE 분모)")
    human_agreement: float | None = Field(
        default=None, description="자동↔사람 라벨 일치율(사람 검증 시)"
    )
    human_sample_size: int | None = Field(default=None)
