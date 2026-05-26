"""HyQE (Hypothetical Question Embedding) 예상질문 생성기.

시설 정보에서 semantic/detail/keyword 3가지 의도 유형의 예상질문을 생성한다.
분포: semantic 50% / detail 30% / keyword 20% (±10% 허용).
분포 불충족 시 1회 재시도 후 템플릿으로 채워 N개 반환.
LLM 실패 시 빈 리스트 반환.
"""

import logging
from typing import Literal

from pydantic import BaseModel

from llm.embedding_config import (
    HYQE_DIST_TOLERANCE,
    HYQE_INTENT_DISTRIBUTION,
    HYQE_QUESTIONS_PER_SERVICE,
)
from llm.prompts.hyqe import HYQE_PROMPT

logger = logging.getLogger(__name__)

# 분포 목표·허용 오차 (embedding_config에서 관리)
_DIST_TARGET: dict[str, float] = HYQE_INTENT_DISTRIBUTION
_DIST_TOLERANCE: float = HYQE_DIST_TOLERANCE

# 템플릿 질문 (분포 부족 시 채우기용 — LLM 실패/재시도 후 최후 수단)
_TEMPLATE_QUESTIONS: dict[str, list[str]] = {
    "semantic": [
        "{service_name}은 어떤 분들이 주로 이용하나요?",
        "{area_name}에서 {min_class_name}을 즐길 수 있는 공공시설 알려주세요.",
        "{service_name} 이용 전 알아두면 좋은 점이 있나요?",
        "{service_name}과 비슷한 {area_name} 시설을 찾고 있어요.",
        "{service_name}은 초보자도 이용하기 좋은 시설인가요?",
    ],
    "detail": [
        "{service_name} 이용 요금은 얼마인가요?",
        "{service_name} 운영 시간은 어떻게 되나요?",
        "{service_name} 예약 접수 방법을 알려주세요.",
        "{service_name} 예약 취소 및 환불 규정을 알고 싶어요.",
        "{service_name} 이용 시 준비물이나 주의사항이 있나요?",
    ],
    "keyword": [
        "{area_name} {min_class_name} 예약",
        "{service_name}",
        "{area_name} {max_class_name} 이용 안내",
        "{min_class_name} 공공시설 {area_name}",
        "{service_name} 이용 정보",
    ],
}


class HyQEQuestion(BaseModel):
    question_text: str
    intent_label: Literal["semantic", "detail", "keyword"]


def _check_distribution(questions: list[HyQEQuestion], n: int) -> bool:
    """분포가 목표 ±tolerance 내에 있으면 True."""
    if not questions:
        return False
    counts: dict[str, int] = {"semantic": 0, "detail": 0, "keyword": 0}
    for q in questions:
        counts[q.intent_label] = counts.get(q.intent_label, 0) + 1
    total = len(questions)
    for label, target_ratio in _DIST_TARGET.items():
        actual_ratio = counts[label] / total
        if abs(actual_ratio - target_ratio) > _DIST_TOLERANCE:
            return False
    return True


def _enforce_distribution(
    questions: list[HyQEQuestion],
    n: int,
    *,
    service_name: str = "",
    area_name: str = "",
    max_class_name: str = "",
    min_class_name: str = "",
) -> list[HyQEQuestion]:
    """분포 강제 후 n개 반환.

    각 레이블의 목표 개수보다 많으면 초과분을 제거하고,
    부족하면 템플릿 질문으로 채운다.
    템플릿의 {service_name} 등 플레이스홀더는 실제 값으로 치환된다.
    """
    target_counts = {
        "semantic": round(n * _DIST_TARGET["semantic"]),
        "detail": round(n * _DIST_TARGET["detail"]),
        "keyword": n
        - round(n * _DIST_TARGET["semantic"])
        - round(n * _DIST_TARGET["detail"]),
    }

    fmt = {
        "service_name": service_name or "해당 시설",
        "area_name": area_name or "서울",
        "max_class_name": max_class_name or "공공시설",
        "min_class_name": min_class_name or "시설",
    }

    # 레이블별 그룹화
    by_label: dict[str, list[HyQEQuestion]] = {
        "semantic": [],
        "detail": [],
        "keyword": [],
    }
    for q in questions:
        by_label[q.intent_label].append(q)

    result: list[HyQEQuestion] = []
    for label, target in target_counts.items():
        existing = by_label[label][:target]
        result.extend(existing)
        shortage = target - len(existing)
        if shortage > 0:
            templates = _TEMPLATE_QUESTIONS[label]
            for i in range(shortage):
                tmpl = templates[i % len(templates)]
                result.append(
                    HyQEQuestion(
                        question_text=tmpl.format(**fmt),
                        intent_label=label,  # type: ignore[arg-type]
                    )
                )

    return result[:n]


async def generate_questions(
    *,
    service_name: str,
    area_name: str | None,
    max_class_name: str | None,
    min_class_name: str | None,
    cleaned_detail: str,
    extracted_summary: str,
    n: int = HYQE_QUESTIONS_PER_SERVICE,
    llm_client,
) -> list[HyQEQuestion]:
    """예상질문 N개 생성.

    LLM 실패 시 빈 리스트 반환.
    분포 불충족 시 1회 재시도 후 _enforce_distribution으로 강제 조정.
    템플릿 폴백 질문의 플레이스홀더({service_name} 등)는 실제 값으로 치환된다.
    """
    from pydantic import TypeAdapter

    list_adapter = TypeAdapter(list[HyQEQuestion])
    chain = HYQE_PROMPT | llm_client.with_structured_output(list_adapter.json_schema())

    input_data = {
        "service_name": service_name,
        "area_name": area_name or "",
        "max_class_name": max_class_name or "",
        "min_class_name": min_class_name or "",
        "extracted_summary": extracted_summary,
        "cleaned_detail": cleaned_detail,
        "n": n,
    }

    _fmt_kwargs = dict(
        service_name=service_name,
        area_name=area_name or "",
        max_class_name=max_class_name or "",
        min_class_name=min_class_name or "",
    )

    questions: list[HyQEQuestion] | None = None

    for attempt in range(2):
        try:
            raw = await chain.ainvoke(input_data)
            if isinstance(raw, list):
                questions = [
                    HyQEQuestion(**q) if isinstance(q, dict) else q for q in raw
                ]
            else:
                questions = []
        except Exception:
            logger.warning(
                "generate_questions 실패 (시도 %d/2)", attempt + 1, exc_info=True
            )
            return []

        if _check_distribution(questions, n):
            return _enforce_distribution(questions, n, **_fmt_kwargs)

        logger.debug(
            "generate_questions 분포 불충족 (시도 %d/2), 재시도 또는 강제 조정",
            attempt + 1,
        )

    # 2회 모두 분포 불충족 → 강제 조정
    return _enforce_distribution(questions or [], n, **_fmt_kwargs)
