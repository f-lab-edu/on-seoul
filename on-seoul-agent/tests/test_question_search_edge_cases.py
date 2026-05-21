"""tools/question_search.py 엣지케이스 단위 테스트.

검증 절차 §2 에서 식별된 누락 경로:
  - SQL에 service_rank = 1 조건이 있는지
  - row_kind = 'question' 만 대상인지 (WHERE 절에 명시적 확인)
"""

from unittest.mock import AsyncMock, MagicMock

from tools.question_search import question_search


_QUESTION_KEYS = ["service_id", "embedding_text", "intent_label", "similarity"]
_SAMPLE_VECTOR = [0.1, 0.2, 0.3]


def _capture_session():
    executed_sql_texts: list[str] = []
    executed_bind_params: list[dict] = []

    async def _capture_execute(stmt, params=None):
        executed_sql_texts.append(str(stmt))
        executed_bind_params.append(params or {})
        mock_result = MagicMock()
        mock_result.keys.return_value = _QUESTION_KEYS
        mock_result.fetchall.return_value = []
        return mock_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_capture_execute)
    return session, executed_sql_texts, executed_bind_params


class TestQuestionSearchSqlStructure:
    async def test_sql_filters_service_rank_equals_1(self):
        """SQL 외부 쿼리에 service_rank = 1 조건이 있어 service_id당 최고 rank만 반환한다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        # service_rank = 1 이 외부 WHERE 절에 있어야 한다
        assert "service_rank = 1" in sql, f"service_rank = 1 조건이 없음:\n{sql}"

    async def test_sql_row_kind_is_question_only(self):
        """SQL WHERE 절에 row_kind = 'question' 조건이 있어 다른 row_kind를 제외한다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        # row_kind = 'question' 필터가 SQL에 포함돼야 한다
        assert "row_kind" in sql, "row_kind 필터가 없음"
        assert "question" in sql, "question 값이 SQL에 없음"

    async def test_sql_order_by_similarity_desc_in_outer_query(self):
        """외부 쿼리에 ORDER BY similarity DESC 절이 있다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "ORDER BY" in sql
        assert "similarity" in sql

    async def test_sql_limit_top_k_in_outer_query(self):
        """외부 쿼리에 LIMIT :top_k 가 있어 최대 반환 건수를 제한한다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "top_k" in sql

    async def test_no_row_kind_identity_or_summary_in_sql(self):
        """SQL에 'identity' 나 'summary' row_kind 값이 포함되지 않아야 한다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "identity" not in sql
        assert "summary" not in sql
