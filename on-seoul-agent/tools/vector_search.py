"""Vector Search Tool — Triple-Track 단일 경쟁 쿼리 + DISTINCT ON dedup.

Phase 1 검색 전략:
  - 모든 row_kind(identity/summary/question)가 단일 HNSW 인덱스에서 경쟁한다.
  - DISTINCT ON (service_id) 로 서비스당 최고 유사도 row 1건으로 중복 제거한다.
  - scan_k = top_k × 12 (시설당 평균 12 row 예상)

post-filter 파라미터(max_class_name, area_name, service_status)는
Phase 1에서는 받되 무시한다. RRF 도입 계획(phase-rrf)에서 복구 예정.

HNSW 파라미터 기준 (Phase 9):
  - m=16, ef_construction=64: 소규모 데이터 기본값.
  - ef_search=40: 정확도 vs 조회 속도 균형.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TOP_K: int = 10
MIN_SIMILARITY: float = 0.6
SCAN_K_MULTIPLIER: int = 12  # scan_k = top_k × 12 (Triple-Track row 수 고려)


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
    # deprecated, ignored in Phase 1 — RRF 계획에서 복구 예정
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
) -> list[dict]:
    """단일 경쟁 쿼리. service_id 기준 DISTINCT ON dedup.

    모든 row_kind(identity/summary/question)가 같은 벡터 공간에서 경쟁하여
    service_id당 최고 유사도 row 1건이 반환된다.

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    query_vector:
        쿼리 임베딩 벡터.
    top_k:
        반환할 최대 결과 수.
    min_similarity:
        코사인 유사도 하한값 (0~1).
    max_class_name, area_name, service_status:
        deprecated, ignored in Phase 1. RRF 계획(phase-rrf)에서 복구 예정.

    Returns
    -------
    list[dict]
        service_id, row_kind, embedding_text, similarity, intent_label 키를 가진 dict 리스트.
        결과 없으면 빈 리스트.
    """
    scan_k = top_k * SCAN_K_MULTIPLIER

    bind = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
        "scan_k": scan_k,
    }

    sql = text("""
        SELECT DISTINCT ON (service_id)
            service_id, row_kind, embedding_text, similarity, intent_label
        FROM (
            SELECT
                service_id, row_kind, embedding_text, intent_label,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) candidates
        ORDER BY service_id, similarity DESC
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
