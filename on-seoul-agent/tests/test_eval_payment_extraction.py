"""scripts/eval/generate_candidates.py — payment 추출 검증 단위 테스트.

DB·LLM 호출 없이 QueryResult → query_conditions.tsv 직렬화 경로만 검증한다.
"""

import csv

from scripts.eval.generate_candidates import QueryResult, _write_query_conditions


def test_query_result_has_extracted_payment_type():
    """QueryResult에 extracted_payment_type 필드가 존재하고 기본값은 빈 문자열."""
    r = QueryResult(query="q", intent="SQL_SEARCH", sub_intent="", refined_query="q")
    assert r.extracted_payment_type == ""


def test_write_query_conditions_includes_payment_column(tmp_path):
    """query_conditions.tsv fieldnames/row에 extracted_payment_type가 기록된다."""
    results = [
        QueryResult(
            query="강남구 무료 문화행사 알려줘",
            intent="SQL_SEARCH",
            sub_intent="",
            refined_query="강남구 무료 문화행사",
            extracted_area_name="강남구",
            extracted_max_class_name="문화체험",
            extracted_payment_type="무료",
        ),
        QueryResult(
            query="마포구 유료 체육시설",
            intent="SQL_SEARCH",
            sub_intent="",
            refined_query="마포구 유료 체육시설",
            extracted_payment_type="유료",
        ),
    ]
    out = tmp_path / "query_conditions.tsv"
    _write_query_conditions(results, out)

    with out.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        assert "extracted_payment_type" in reader.fieldnames
        rows = list(reader)
    assert rows[0]["extracted_payment_type"] == "무료"
    assert rows[1]["extracted_payment_type"] == "유료"
