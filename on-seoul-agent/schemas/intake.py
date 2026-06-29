"""intake_node 구조화 출력 스키마 (reference_resolution + triage 병합).

intake_node 는 턴 1회 with_structured_output 호출로 "이 턴이 무엇인가"를 끝낸다.
  - turn_kind: 1차 분기 스위치 (REFINE/DRILL/RELEVANCE/META/NEW)
  - action:    turn_kind=NEW 일 때만 의미 (RETRIEVE/DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE)
  - oos_type:  action=OUT_OF_SCOPE 일 때 서브타입 (operational_detail 신설)
  - ref_indices: 1-based prev_entities 인덱스 (LLM 은 인덱스만 선택, service_id 생성 금지)
  - user_rationale: sanitize 후 decision 이벤트로

핵심 불변식(인덱스 계약):
    LLM 은 service_id 를 *생성*하지 않고 prev_entities 의 1-based 인덱스만 선택한다.
    런타임이 범위 검증 후 배열에서 실제 service_id 를 읽는다 → ID 환각 0
    (매핑·검증은 agents/_intake_indexing.py 의 순수 함수).
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TurnKind(str, Enum):
    """턴 성격 — intake 1차 분기 스위치 (= 기존 follow_up_type)."""

    NEW = "NEW"  # 신규 질문 (action 위임). 누락·미지·파싱 실패 시 기본값.
    REFINE = "REFINE"  # 직전 결과에 제약 추가 → working_set_refine_node 재검색
    DRILL = "DRILL"  # 개별 상세 → rehydrate(단건) → describe
    # 집합 적합성 → rehydrate(집합) → describe. "적합성 변형"은 후속 단계로 이연이라
    # 현재는 describe 가 공용 서술을 재사용한다(drift 오해 방지 — 변형은 후속에서 분기).
    RELEVANCE = "RELEVANCE"
    META = "META"  # 판단 근거 → explain (기존 EXPLAIN 흡수)


class IntakeAction(str, Enum):
    """turn_kind=NEW 일 때의 행동 유형 (기존 ActionType 5종 중 EXPLAIN 제외 4종).

    EXPLAIN 은 turn_kind=META 로 승격되어 여기서 제외된다.
    """

    RETRIEVE = "RETRIEVE"
    DIRECT_ANSWER = "DIRECT_ANSWER"
    AMBIGUOUS = "AMBIGUOUS"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


# OUT_OF_SCOPE 서브타입. operational_detail 신설(운영-상세 답변가능 분기):
# 폭염·휴무·주차·우천 등은 attribute_gap 이 아니라 operational_detail 로 분리한다.
OosType = Literal["domain_outside", "attribute_gap", "operational_detail"]


class IntakeOutput(BaseModel):
    """intake_node 구조화 출력 — with_structured_output 바인딩용.

    turn_kind 가 1차 스위치이고, NEW 일 때만 action 이 의미를 갖는다.
    ref_indices 는 prev_entities 의 1-based 인덱스(NEW/META 면 빈 리스트).
    """

    reasoning: str | None = Field(
        default=None,
        description="분류 근거 (CoT, 내부 전용)",
    )
    turn_kind: TurnKind = Field(
        default=TurnKind.NEW,
        description="턴 성격 (1차 분기). 불확실하면 NEW.",
    )
    action: IntakeAction = Field(
        default=IntakeAction.RETRIEVE,
        description="turn_kind=NEW 일 때만 의미. 불확실하면 RETRIEVE.",
    )
    oos_type: OosType | None = Field(
        default=None,
        description="action=OUT_OF_SCOPE 일 때 서브타입",
    )
    ref_indices: list[int] = Field(
        default_factory=list,
        description="prev_entities 의 1-based 인덱스. NEW/META 면 빈 리스트. service_id 생성 금지.",
    )
    user_rationale: str | None = Field(
        default=None,
        description="사용자에게 보여줄 판단 근거 1문장",
    )
