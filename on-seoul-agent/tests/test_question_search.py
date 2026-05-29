"""tools/question_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB 및 OpenAI/Gemini API에 접근하지 않는다.

쿼리 구조 (DISTINCT ON 패턴):
    SELECT DISTINCT ON (service_id) ...
    FROM service_embeddings
    WHERE row_kind = 'question'
      AND 1 - (embedding <=> :query_vector) >= :min_similarity
    ORDER BY service_id, embedding <=> :query_vector
    LIMIT :top_k
"""

from unittest.mock import AsyncMock, MagicMock

from tools.question_search import question_search
from tools.vector_search import MIN_SIMILARITY


_QUESTION_KEYS = ["service_id", "embedding_text", "intent_label", "similarity"]


def _make_session(rows: list[dict]) -> MagicMock:
    """fake AsyncSession. execute 호출 시 rows를 반환한다."""
    mock_result = MagicMock()
    if rows:
        mock_result.keys.return_value = list(rows[0].keys())
        mock_result.fetchall.return_value = [tuple(r.values()) for r in rows]
    else:
        mock_result.keys.return_value = _QUESTION_KEYS
        mock_result.fetchall.return_value = []
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    return mock_session


def _capture_session() -> tuple[MagicMock, list[str], list[dict]]:
    """SQL 텍스트와 bind 파라미터를 캡처하는 세션을 반환한다."""
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


_SAMPLE_VECTOR = [0.1, 0.2, 0.3]

_SAMPLE_ROWS = [
    {
        "service_id": "S001",
        "embedding_text": "이용 요금이 얼마인가요?",
        "intent_label": "detail",
        "similarity": 0.88,
    },
    {
        "service_id": "S002",
        "embedding_text": "예약 취소 기한이 어떻게 되나요?",
        "intent_label": "detail",
        "similarity": 0.82,
    },
]


class TestQuestionSearch:
    async def test_empty_result_returns_empty_list(self):
        """결과가 없을 때 빈 리스트를 반환한다."""
        session = _make_session([])
        result = await question_search(session, _SAMPLE_VECTOR)
        assert result == []
        assert result is not None

    async def test_only_queries_question_rows(self):
        """SQL에 row_kind='question' 조건이 포함된다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "question" in texts[0]

    async def test_min_similarity_filter(self):
        """min_similarity가 bind에 전달된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "min_similarity" in binds[0]
        assert binds[0]["min_similarity"] == MIN_SIMILARITY

    async def test_returns_intent_label(self):
        """반환 결과에 intent_label 필드가 포함된다."""
        session = _make_session([_SAMPLE_ROWS[0]])
        result = await question_search(session, _SAMPLE_VECTOR)
        assert len(result) == 1
        assert "intent_label" in result[0]

    async def test_distinct_on_service_id_in_sql(self):
        """SQL에 DISTINCT ON (service_id) 패턴이 포함된다 (service_id당 최고 유사도 1건)."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0].upper()
        assert "DISTINCT ON" in sql
        assert "SERVICE_ID" in sql

    async def test_no_scan_k_in_bind(self):
        """scan_k 파라미터가 제거됐으므로 bind에 포함되지 않는다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "scan_k" not in binds[0]

    async def test_custom_top_k_override(self):
        """top_k를 명시적으로 전달하면 bind에 반영된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR, top_k=5)
        assert binds[0]["top_k"] == 5

    async def test_order_by_service_id_and_distance(self):
        """ORDER BY service_id, embedding <=> vector 패턴이 포함된다 (partial index 활용)."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "ORDER BY" in sql
        assert "service_id" in sql
        assert "<=>" in sql

    async def test_query_vector_in_bind(self):
        """query_vector가 str로 변환되어 bind에 전달된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert binds[0]["query_vector"] == str(_SAMPLE_VECTOR)

    async def test_result_fields_complete(self):
        """반환 결과에 service_id, embedding_text, intent_label, similarity가 모두 있다."""
        session = _make_session(_SAMPLE_ROWS)
        result = await question_search(session, _SAMPLE_VECTOR)
        assert len(result) == 2
        for row in result:
            assert set(row.keys()) == {"service_id", "embedding_text", "intent_label", "similarity"}
