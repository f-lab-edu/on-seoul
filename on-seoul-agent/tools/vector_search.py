"""Vector Search Tool — pgvector 코사인 유사도 검색 (post-filter 지원).

검색 전략 결정 (Phase 15):
  - post-filter 전략 채택: 전체 임베딩에서 HNSW 인덱스로 scan_k건을 먼저 추출한 뒤
    서브쿼리 외부에서 metadata 필터를 적용한다.
  - pre-filter 제거 이유: pgvector HNSW 인덱스는 WHERE 조건과 동시에 적용 시
    sequential scan으로 폴백하여 인덱스 효과가 없어진다.
  - scan_k = top_k × SCAN_K_MULTIPLIER(기본 5): 필터 탈락으로 인한 결과 부족을 완충.

HNSW 파라미터 기준 (Phase 9):
  - m=16, ef_construction=64: 소규모 데이터(1000건) 기본값. 품질 vs 빌드 비용 균형.
  - ef_search=40: 정확도 vs 조회 속도 균형.
  - 10000건 이상 시 m=32, ef_construction=128 조정 권고.
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TOP_K: int = 10
MIN_SIMILARITY: float = 0.6
SCAN_K_MULTIPLIER: int = 5  # scan_k = top_k × SCAN_K_MULTIPLIER

# post-filter로 허용되는 metadata 필드 화이트리스트.
# 아래 상수에서만 WHERE 절 조건 문자열을 조립하며, 런타임 외부 값은 bind 파라미터로만 전달한다.
# 새 필드를 추가할 때는 반드시 정적 조건 문자열과 함께 이 상수에 등록해야 한다.
_ALLOWED_POSTFILTER_CLAUSES: dict[str, str] = {
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
    """pgvector 코사인 유사도 검색 (post-filter 방식).

    내부 서브쿼리에서 필터 없이 전체 임베딩 대상으로 HNSW 인덱스를 활용해
    scan_k(top_k × SCAN_K_MULTIPLIER)건을 추출한다. 서브쿼리 외부에서
    max_class_name·area_name·service_status 필터를 적용한다.

    필터 값은 모두 bind 파라미터로 전달한다 (SQL injection 방지).
    필터가 None이면 해당 조건을 WHERE 절에 추가하지 않는다.

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    query_vector:
        쿼리 임베딩 벡터.
    max_class_name:
        대분류 post-filter (metadata->>'max_class_name'). None이면 미적용.
    area_name:
        지역 post-filter (metadata->>'area_name'). None이면 미적용.
    service_status:
        예약 상태 post-filter (metadata->>'service_status'). None이면 미적용.
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
    scan_k = top_k * SCAN_K_MULTIPLIER

    # post-filter 조건을 화이트리스트(_ALLOWED_POSTFILTER_CLAUSES)에서 조립한다.
    # 조건 문자열은 상수에서만 가져오며, 필터 값은 bind 파라미터로만 전달한다.
    filter_inputs: dict[str, str | None] = {
        "max_class_name": max_class_name,
        "area_name": area_name,
        "service_status": service_status,
    }
    post_filter_clauses: list[str] = []
    bind: dict[str, Any] = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
        "scan_k": scan_k,
    }

    for field, value in filter_inputs.items():
        if value is not None:
            post_filter_clauses.append(_ALLOWED_POSTFILTER_CLAUSES[field])
            bind[field] = value

    # min_similarity와 post-filter 조건을 candidates 서브쿼리 외부에 조립한다.
    # HNSW scan_k 완충 버퍼가 정상 동작하려면 서브쿼리 내부에 필터를 두지 않아야 한다.
    outer_clauses: list[str] = ["similarity >= :min_similarity"]
    outer_clauses.extend(post_filter_clauses)
    outer_where_sql = "WHERE " + " AND ".join(outer_clauses)

    sql = text(f"""
        SELECT service_id, service_name, metadata, similarity
        FROM (
            SELECT
                service_id,
                service_name,
                metadata,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) candidates
        {outer_where_sql}
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
