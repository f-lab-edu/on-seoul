"""retrieval_critic_node 구조화 출력 스키마 (L1 retrieval-critic).

critic 은 검색 결과가 약할 때(0건/thin/skew) "다음에 무엇을 할지"를 정하는 최초의
LLM 판단 루프다. 출력은 세 가지로만 제약된다:

  - decision: ANSWER / REPLAN / STOP (observe→decide 루프의 최소 계약)
  - replan_hint: REPLAN 일 때만. 재탐색 *방향* 힌트
  - rationale: decision 이벤트용 근거 1문장(내부 식별자 제거는 상위 노드 책임)

핵심 불변식(인젝션 가드):
    critic 은 자유 SQL/컬럼/식별자를 *생성하지 않는다*. replan_hint 는 스키마 레벨에서
    IntentType enum + 화이트리스트 필터명(Literal) + 자연어 재구성 문자열로만 표현
    가능하다. 자유 컬럼/식별자는 애초에 이 타입으로 담길 수 없다. 실제 파라미터화는
    router 가 검증 후 수행하고, tools/ 는 그대로 파라미터 바인딩 + 화이트리스트로 조회한다.

이 모듈은 critic 판단 스키마를 정의한다 — critic 노드 구현과 그래프 배선은 별도 모듈에서 다룬다.
"""

from enum import Enum
from typing import Literal, get_args

from pydantic import BaseModel, Field

from schemas.state import FilterState, IntentType


class CriticDecision(str, Enum):
    """critic 3택 — 검색 결과를 보고 정하는 다음 행동."""

    ANSWER = "ANSWER"  # 결과로 답한다(REPLAN 무의미 판단 포함 — thin/skew 톤 조정)
    REPLAN = "REPLAN"  # 방향을 바꿔 재탐색한다(replan_hint 소비)
    STOP = "STOP"  # 정직한 한계 안내로 종료(예산 소진/개선 불가)


# 화이트리스트 필터명 — FilterState(post-filter) 키와 1:1 정합.
# critic 이 드롭을 제안할 수 있는 필터는 이 목록뿐이다(단일 출처). FilterState 가
# 바뀌면 이 Literal 도 같이 바뀌어야 하며, 그 정합을 테스트가 강제한다.
DropFilterName = Literal[
    "max_class_name",
    "area_name",
    "service_status",
    "payment_type",
    "target_audience",
]

# 런타임 검증/테스트용 튜플(계약 정합 확인). Literal 인자를 그대로 노출한다.
ALLOWED_DROP_FILTERS: tuple[str, ...] = get_args(DropFilterName)

# 정합 가드(임포트 시점) — DropFilterName 화이트리스트가 FilterState 키를 벗어나면
# 즉시 실패시킨다. critic 힌트가 존재하지 않는 필터를 지시하는 인젝션 표면을 막는다.
assert set(ALLOWED_DROP_FILTERS) == set(FilterState.__annotations__.keys()), (
    "DropFilterName 화이트리스트가 FilterState 키와 어긋났습니다 — 계약 동시 갱신 필요."
)


class ReplanHint(BaseModel):
    """REPLAN 재탐색 방향 힌트 — retry_prep_node 가 소비한다.

    인젝션 가드: 모든 필드가 enum / 화이트리스트 Literal / 자연어 문자열로만 제약된다.
    자유 SQL·컬럼·식별자는 이 타입으로 표현 불가하다.
    """

    intent: IntentType | None = Field(
        default=None,
        description="전환할 검색 intent(화이트리스트 enum). 유지 시 None.",
    )
    drop_filters: list[DropFilterName] | None = Field(
        default=None,
        description="드롭할 post-filter 명(화이트리스트만). 자유 컬럼/식별자 금지.",
    )
    reformulate_query: str | None = Field(
        default=None,
        description="벡터 검색용 재구성 자연어 질의(SQL 아님). 불필요 시 None.",
    )
    reason: str = Field(
        description="이 방향으로 재탐색하는 이유 1문장(내부 근거).",
    )


class CriticOutput(BaseModel):
    """retrieval_critic_node 구조화 출력 — with_structured_output(CriticOutput) 바인딩용.

    decision 이 3택 스위치이고, REPLAN 일 때만 replan_hint 가 의미를 갖는다.
    (REPLAN 인데 replan_hint 가 없으면 상위 노드가 결정적 폴백으로 처리.)
    """

    decision: CriticDecision = Field(
        description="ANSWER / REPLAN / STOP 중 하나.",
    )
    replan_hint: ReplanHint | None = Field(
        default=None,
        description="decision=REPLAN 일 때 재탐색 방향. 그 외 None.",
    )
    rationale: str = Field(
        description="사용자/관측용 근거 1문장(내부 식별자 제거는 상위 노드 책임).",
    )
