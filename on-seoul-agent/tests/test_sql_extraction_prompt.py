"""sql_extraction 프롬프트 — payment_type 추출 지시·few-shot 정합성 검증.

MUST-FIX(eval-운영 divergence) 가드: SqlAgent의 else 브랜치(refined_query 없음,
eval _run_sql 경로)에서 LLM이 payment_type을 추출하려면 system 지시 + few-shot
출력 JSON에 payment_type이 실려 있어야 한다. 이 테스트는 그 계약을 고정한다.

LLM·DB 호출 없이 프롬프트 상수만 검사한다.
"""

import json

from llm.prompts.sql_extraction import (
    SQL_EXTRACTION_FEW_SHOT_EXAMPLES,
    SQL_EXTRACTION_SYSTEM,
)


def test_system_prompt_instructs_payment_type_extraction():
    """system 프롬프트에 payment_type enum(무료/유료) 매핑 지시가 있다."""
    assert "payment_type" in SQL_EXTRACTION_SYSTEM
    assert "무료" in SQL_EXTRACTION_SYSTEM
    assert "유료" in SQL_EXTRACTION_SYSTEM


def test_every_few_shot_output_includes_payment_type_key():
    """모든 few-shot 출력 JSON이 payment_type 키를 명시한다(일관성)."""
    for ex in SQL_EXTRACTION_FEW_SHOT_EXAMPLES:
        parsed = json.loads(ex["output"])
        assert "payment_type" in parsed, ex["message"]


def test_free_culture_example_extracts_payment_type():
    """'강남구 무료 문화행사' 예시가 payment_type='무료'를 산출하고 keyword엔 없음."""
    target = next(
        ex
        for ex in SQL_EXTRACTION_FEW_SHOT_EXAMPLES
        if ex["message"] == "강남구 무료 문화행사 알려줘"
    )
    parsed = json.loads(target["output"])
    assert parsed["payment_type"] == "무료"
    assert parsed["max_class_name"] == "문화체험"
    assert parsed["area_name"] == "강남구"
    # "무료"는 keyword가 아니라 payment_type으로 분류되어야 한다
    assert parsed["keyword"] is None
