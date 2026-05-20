"""Question Search Tool — Track C FAQ 질문 트랙 검색.

service_embeddings WHERE row_kind='question' 에서 service_id당 최고 유사도 row 1건만 반환.
PARTITION BY service_id ROW_NUMBER() 윈도우 함수로 중복을 제거한다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from tools.vector_search import MIN_SIMILARITY, TOP_K


async def question_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    scan_k: int | None = None,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[dict]:
    """service_embeddings WHERE row_kind='question' → service_id별 최고 rank 1건만 반환.

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    query_vector:
        쿼리 임베딩 벡터.
    scan_k:
        ANN 후보 수. None이면 rrf_scan_k_per_track × question_scan_multiplier.
    top_k:
        반환할 최대 결과 수.
    min_similarity:
        코사인 유사도 하한값 (0~1). 서브쿼리 내부 필터로 적용.

    Returns
    -------
    list[dict]
        service_id, embedding_text, intent_label, similarity 키를 가진 dict 리스트.
        service_id당 최고 유사도 row 1건. 결과 없으면 빈 리스트.
    """
    if scan_k is None:
        scan_k = settings.rrf_scan_k_per_track * settings.question_scan_multiplier

    bind = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
        "scan_k": scan_k,
    }

    sql = text("""
        WITH ranked AS (
            SELECT
                service_id,
                embedding_text,
                intent_label,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity,
                ROW_NUMBER() OVER (
                    PARTITION BY service_id
                    ORDER BY embedding <=> CAST(:query_vector AS vector)
                ) AS service_rank
            FROM service_embeddings
            WHERE row_kind = 'question'
              AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        )
        SELECT service_id, embedding_text, intent_label, similarity
        FROM ranked
        WHERE service_rank = 1
        ORDER BY similarity DESC
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
