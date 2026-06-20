"""Question Search Tool — Track C FAQ 질문 트랙 검색.

service_embeddings WHERE row_kind='question' 에서 service_id당 최고 유사도 row 1건만 반환.
DISTINCT ON (service_id) 패턴으로 중복을 제거한다.

인덱스:
    idx_se_question_service_id (scripts/ddl_indexes_on_ai.sql)
    — partial index (WHERE row_kind='question'), service_id 정렬 기반.
    — Incremental Sort → Unique → Limit 조기 종료 구조로 전체 스캔을 방지한다.

이전 설계와의 차이:
    구버전은 ROW_NUMBER() 윈도우 + scan_k LIMIT 조합이었으나,
    윈도우 함수가 HNSW ANN 최적화를 막고 WindowAgg + 중첩 Sort 구조로 실행됐다.
    DISTINCT ON 으로 전환한 뒤 실측 결과 Execution Time 이 소폭 개선되고
    실행 계획이 단순해졌다. scan_k 파라미터도 제거했다.
    서브쿼리로 감싸 outer ORDER BY similarity DESC + LIMIT :top_k 를 적용함으로써
    service_id 알파벳 순이 아닌 유사도 내림차순으로 최종 결과를 정렬한다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings


async def question_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    top_k: int | None = None,
    min_similarity: float | None = None,
) -> list[dict]:
    """service_embeddings WHERE row_kind='question' → service_id별 최고 유사도 1건 반환.

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings 읽기 권한).
    query_vector:
        쿼리 임베딩 벡터.
    top_k:
        반환할 최대 결과 수. None이면 settings.vector_track_top_k 사용.
    min_similarity:
        코사인 유사도 하한값 (0~1).
        None이면 settings.vector_min_similarity_question 사용.

    Returns
    -------
    list[dict]
        service_id, embedding_text, intent_label, similarity 키를 가진 dict 리스트.
        service_id당 최고 유사도 row 1건. 결과 없으면 빈 리스트.
    """
    if not query_vector or not all(isinstance(v, (int, float)) for v in query_vector):
        raise ValueError("query_vector must be a non-empty list of floats")
    if top_k is None:
        top_k = settings.vector_track_top_k
    if min_similarity is None:
        min_similarity = settings.vector_min_similarity_question

    bind = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
    }

    sql = text("""
        SELECT * FROM (
            SELECT DISTINCT ON (service_id)
                service_id,
                embedding_text,
                intent_label,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE row_kind = 'question'
              AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
            ORDER BY service_id, embedding <=> CAST(:query_vector AS vector)
        ) ranked
        ORDER BY similarity DESC
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
