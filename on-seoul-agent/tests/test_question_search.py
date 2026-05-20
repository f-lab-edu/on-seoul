"""tools/question_search.py 단위 테스트.

Mock DB 세션으로 SQL 실행 경로와 bind 파라미터를 검증한다.
실제 DB 및 OpenAI/Gemini API에 접근하지 않는다.
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
        "service_id": "S001",  # 같은 service_id — 중복
        "embedding_text": "할인 요금은?",
        "intent_label": "detail",
        "similarity": 0.75,
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
        single_row = [_SAMPLE_ROWS[0]]
        session = _make_session(single_row)
        result = await question_search(session, _SAMPLE_VECTOR)
        assert len(result) == 1
        assert "intent_label" in result[0]

    async def test_dedup_per_service_id(self):
        """SQL에 PARTITION BY service_id 로 service_id당 최고 rank 1건만 반환한다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        sql = texts[0]
        assert "PARTITION BY" in sql
        assert "service_id" in sql

    async def test_scan_k_in_bind(self):
        """scan_k가 bind에 포함된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "scan_k" in binds[0]
        assert binds[0]["scan_k"] > 0

    async def test_custom_scan_k_override(self):
        """scan_k를 명시적으로 전달하면 bind에 반영된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR, scan_k=100)
        assert binds[0]["scan_k"] == 100

    async def test_custom_top_k_override(self):
        """top_k를 명시적으로 전달하면 bind에 반영된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR, top_k=5)
        assert binds[0]["top_k"] == 5

    async def test_sql_uses_row_number_ranking(self):
        """SQL에 ROW_NUMBER() 윈도우 함수가 포함된다 (service_id당 최고 rank 선택)."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "ROW_NUMBER" in texts[0]

    async def test_with_ranked_cte(self):
        """SQL에 WITH ranked AS (...) CTE가 포함된다."""
        session, texts, _ = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert "ranked" in texts[0]

    async def test_query_vector_in_bind(self):
        """query_vector가 str로 변환되어 bind에 전달된다."""
        session, _, binds = _capture_session()
        await question_search(session, _SAMPLE_VECTOR)
        assert binds[0]["query_vector"] == str(_SAMPLE_VECTOR)
