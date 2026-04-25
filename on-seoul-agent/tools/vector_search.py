"""Vector Search Tool — pgvector 코사인 유사도 검색 (pre-filter 지원).

검색 전략 결정 (Phase 8):
  - pre-filter 전략 채택: 카테고리/지역/상태 필터를 벡터 검색 전 WHERE 절에 적용.
    이유: 1000건 미만 데이터에서 관련 카테고리 내 유사도 비교로 정확도 향상.
  - 하이브리드 검색(tsvector) 미채택: 소규모 데이터에서 순수 벡터 검색 품질 충분.
    데이터 5000건 이상 시 도입 검토.
  - 재순위화/MMR 미채택: top-k=10, min_similarity=0.6 기준 중복 발생 빈도 낮음.

HNSW 파라미터 기준 (Phase 9):
  - m=16, ef_construction=64: 소규모 데이터(1000건) 기본값. 품질 vs 빌드 비용 균형.
  - ef_search=40: 정확도 vs 조회 속도 균형.
  - 10000건 이상 시 m=32, ef_construction=128 조정 권고.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TOP_K: int = 10
MIN_SIMILARITY: float = 0.6

# pre-filter로 허용되는 metadata 필드 화이트리스트.
# 아래 상수에서만 WHERE 절 조건 문자열을 조립하며, 런타임 외부 값은 bind 파라미터로만 전달한다.
# 새 필드를 추가할 때는 반드시 정적 조건 문자열과 함께 이 상수에 등록해야 한다.
_ALLOWED_PREFILTER_CLAUSES: dict[str, str] = {
    "max_class_name": "metadata->>'max_class_name' = :max_class_name",
    "area_name":      "metadata->>'area_name' = :area_name",
    "service_status": "metadata->>'service_status' = :service_status",
}


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[dict]:
    """pgvector 코사인 유사도 검색.

    pre-filter 조건(max_class_name, area_name, service_status)은 WHERE 절에 정적
    JSONB 경로로 적용한다. 값은 모두 bind 파라미터로 전달한다 (SQL injection 방지).

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    query_vector:
        쿼리 임베딩 벡터.
    max_class_name:
        대분류 필터 (metadata->>'max_class_name'). None이면 미적용.
    area_name:
        지역 필터 (metadata->>'area_name'). None이면 미적용.
    service_status:
        예약 상태 필터 (metadata->>'service_status'). None이면 미적용.
    top_k:
        반환할 최대 결과 수.
    min_similarity:
        코사인 유사도 하한값 (0~1).

    Returns
    -------
    list[dict]
        service_id, service_name, metadata, similarity 키를 가진 딕셔너리 리스트.
        결과 없으면 빈 리스트.
    """
    # pre-filter 조건을 화이트리스트(_ALLOWED_PREFILTER_CLAUSES)에서 조립한다.
    # 조건 문자열은 상수에서만 가져오며, 필터 값은 bind 파라미터로만 전달한다.
    filter_inputs: dict[str, str | None] = {
        "max_class_name": max_class_name,
        "area_name": area_name,
        "service_status": service_status,
    }
    pre_filter_clauses: list[str] = []
    bind: dict = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
    }

    for field, value in filter_inputs.items():
        if value is not None:
            pre_filter_clauses.append(_ALLOWED_PREFILTER_CLAUSES[field])
            bind[field] = value

    pre_filter_sql = ""
    if pre_filter_clauses:
        pre_filter_sql = " AND " + " AND ".join(pre_filter_clauses)

    sql = text(f"""
        SELECT
            service_id,
            service_name,
            metadata,
            1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
        FROM service_embeddings
        WHERE 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
        {pre_filter_sql}
        ORDER BY embedding <=> CAST(:query_vector AS vector)
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
