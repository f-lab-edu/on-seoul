"""평가셋 ②(회귀/품질 고정 데이터셋) 스캐폴딩 + Langfuse Dataset 등록.

큐레이션 + 합성 케이스를 Langfuse Dataset 으로 고정한다. 각 케이스에
기대 동작(intent, critic 발동 여부, 최소 결과 건수, 필수 시설) 라벨을 붙여
baseline↔after 채점(결정적 체크 + LLM-as-judge)에 재사용한다.

케이스 패밀리(요구된 유형 전부 최소 1건):
  - simple_no_critic : 단순 대표 — critic **미발동** 회귀 가드(80% 경로 보호 확인).
  - thin             : 빈약 결과 유발.
  - skew             : 한 구/카테고리 쏠림 유발.
  - zero_hit         : 0건 유발.
  - intent_mispick   : intent 오선택 유발(집계 질의 등).
  - drift            : 결과 표류 유발(자연 활동 ↔ 실내 강좌 혼입).

expected_output 스키마가 곧 채점 계약이다 — 채점기가 이 dict 를 읽는다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvalCase(BaseModel):
    """고정 평가 케이스 — 질의 + 기대 동작 라벨."""

    family: str = Field(description="실패 패밀리(simple_no_critic/thin/skew/...)")
    query: str = Field(description="사용자 질의(입력)")
    expected_intent: str = Field(description="기대 intent")
    expected_critic_fires: bool = Field(
        description="critic 발동 기대 여부(단순 케이스는 False = 미발동 회귀 가드)"
    )
    min_results: int = Field(default=0, description="채점 시 요구되는 최소 결과 건수")
    required_services: list[str] = Field(
        default_factory=list, description="반드시 포함되어야 할 service_id(있으면)"
    )
    note: str = Field(default="", description="케이스 의도 메모")


# 합성/큐레이션 고정 케이스. 서울 공공서비스 예약 도메인 기준.
# service_id 는 데이터셋 변동에 취약하므로 기본은 비워 두고(min_results·intent 로 채점),
# 안정적 필수 시설이 확정되면 required_services 에 채운다.
CURATED_CASES: list[EvalCase] = [
    # --- simple_no_critic: critic 미발동 회귀 가드 ---
    EvalCase(
        family="simple_no_critic",
        query="강남구 수영장 알려줘",
        expected_intent="SQL_SEARCH",
        expected_critic_fires=False,
        min_results=1,
        note="단순 지역+시설 — 결과 충분, critic 미호출(빠른 80% 경로).",
    ),
    EvalCase(
        family="simple_no_critic",
        query="지금 예약 가능한 문화행사 보여줘",
        expected_intent="SQL_SEARCH",
        expected_critic_fires=False,
        min_results=1,
        note="상태 필터 단순 조회 — critic 미호출.",
    ),
    # --- thin: 빈약 결과 ---
    EvalCase(
        family="thin",
        query="한강 근처 야간 개장 실내 클라이밍장",
        expected_intent="VECTOR_SEARCH",
        expected_critic_fires=True,
        min_results=0,
        note="희소 조합 — 1~2건 thin 유발, critic 이 재탐색/톤 판단.",
    ),
    # --- skew: 쏠림 ---
    EvalCase(
        family="skew",
        query="서울 무료 전시 추천",
        expected_intent="VECTOR_SEARCH",
        expected_critic_fires=True,
        min_results=3,
        note="결과가 한 구에 쏠릴 개연 — skew 유발, critic 재탐색 판단.",
    ),
    # --- zero_hit: 0건 ---
    EvalCase(
        family="zero_hit",
        query="은평구 심야 승마 강습 예약",
        expected_intent="SQL_SEARCH",
        expected_critic_fires=True,
        min_results=0,
        note="존재 개연 낮은 조합 — 0건 유발, critic 이 필터 완화/재구성 판단.",
    ),
    # --- intent_mispick: intent 오선택 유발 ---
    EvalCase(
        family="intent_mispick",
        query="자치구별 체육시설 예약 건수 순위 알려줘",
        expected_intent="ANALYTICS",
        expected_critic_fires=True,
        min_results=1,
        note="집계 질의 — SQL_SEARCH 로 오선택되기 쉬움. critic 이 intent 재선택 유도.",
    ),
    # --- drift: 결과 표류 ---
    EvalCase(
        family="drift",
        query="자연 속에서 할 수 있는 활동 추천해줘",
        expected_intent="VECTOR_SEARCH",
        expected_critic_fires=True,
        min_results=1,
        note="실내 강좌가 혼입되기 쉬움(표류). critic 이 reformulate 판단.",
    ),
]


def _to_item(case: EvalCase) -> dict[str, Any]:
    """EvalCase → Langfuse dataset item 페이로드(input/expected_output/metadata)."""
    return {
        "input": {"message": case.query},
        "expected_output": {
            "expected_intent": case.expected_intent,
            "expected_critic_fires": case.expected_critic_fires,
            "min_results": case.min_results,
            "required_services": case.required_services,
        },
        "metadata": {"family": case.family, "note": case.note},
    }


def push_dataset(
    cases: list[EvalCase],
    *,
    client: Any | None,
    dataset_name: str,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """케이스를 Langfuse Dataset 으로 등록하고 등록 페이로드를 반환한다.

    dry_run=True 또는 client=None 이면 실제 등록 없이 페이로드만 산출(파이프라인 증명).
    라이브 등록은 client.create_dataset(name) + create_dataset_item(...) 를 호출한다.
    """
    items = [_to_item(c) for c in cases]

    if dry_run or client is None:
        return items

    client.create_dataset(name=dataset_name)
    for item in items:
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )
    return items
