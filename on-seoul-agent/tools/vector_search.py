"""Vector Search Tool — row_kind 파라미터 기반 트랙별 검색.

Phase RRF 검색 전략:
  - row_kind='identity' : 시설 기본 정보 트랙 (Track A). post-filter 적용.
  - row_kind='summary'  : 상세 요약 트랙 (Track B). post-filter 미적용.
  - row_kind='question' : FAQ 질문 트랙 (Track C) — question_search 도구 사용.

각 트랙은 독립 쿼리로 실행된 후 RRF로 결합된다.
scan_k는 settings.rrf_scan_k_per_track 을 사용한다.

HNSW 파라미터 기준 (Phase 9):
  - m=16, ef_construction=64: 소규모 데이터 기본값.
  - ef_search=40: 정확도 vs 조회 속도 균형.
"""

from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings

_ALLOWED_ROW_KIND: frozenset[str] = frozenset({"identity", "summary"})


def resolve_min_similarity(row_kind: str) -> float:
    """row_kind별 운영 min_similarity 하한을 config에서 조회한다."""
    return {
        "identity": settings.vector_min_similarity_identity,
        "summary": settings.vector_min_similarity_summary,
        "question": settings.vector_min_similarity_question,
    }[row_kind]


async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    row_kind: Literal["identity", "summary"] = "identity",
    top_k: int | None = None,
    min_similarity: float | None = None,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
) -> list[dict]:
    """row_kind 파라미터로 트랙을 지정하는 벡터 유사도 검색.

    Parameters
    ----------
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    query_vector:
        쿼리 임베딩 벡터.
    row_kind:
        검색 대상 트랙. 'identity' 또는 'summary'. 기본값 'identity'.
        'question' 트랙은 question_search 도구를 사용한다.
    top_k:
        반환할 최대 결과 수. None이면 settings.vector_track_top_k 사용.
    min_similarity:
        코사인 유사도 하한값 (0~1). 서브쿼리 내부 필터로 적용.
        None이면 row_kind별 config 값 사용 (vector_min_similarity_*).
    max_class_name, area_name, service_status:
        post-filter 파라미터. identity row에만 적용.
        summary row는 metadata가 NULL이므로 파라미터를 전달해도 무시한다.

    Returns
    -------
    list[dict]
        service_id, embedding_text, metadata, similarity 키를 가진 dict 리스트.
        결과 없으면 빈 리스트.

    Raises
    ------
    ValueError
        row_kind가 'identity' 또는 'summary'가 아닐 때.
    """
    if row_kind not in _ALLOWED_ROW_KIND:
        raise ValueError(
            f"invalid row_kind: {row_kind!r}. 허용 값: {sorted(_ALLOWED_ROW_KIND)}"
        )

    if top_k is None:
        top_k = settings.vector_track_top_k
    if min_similarity is None:
        min_similarity = resolve_min_similarity(row_kind)

    scan_k = settings.rrf_scan_k_per_track

    bind: dict = {
        "query_vector": str(query_vector),
        "min_similarity": min_similarity,
        "top_k": top_k,
        "scan_k": scan_k,
        "row_kind": row_kind,
    }

    # post-filter: None인 경우 조건 자체를 생략한다.
    # asyncpg 파라미터 타입 추론 문제(AmbiguousParameterError) 방지:
    # None을 바인드 파라미터로 전달하면 PostgreSQL이 $N의 타입을 결정할 수 없다.
    post_filters: list[str] = []
    if max_class_name is not None:
        post_filters.append("metadata->>'max_class_name' = :max_class_name")
        bind["max_class_name"] = max_class_name
    if area_name is not None:
        post_filters.append("metadata->>'area_name' = :area_name")
        bind["area_name"] = area_name
    if service_status is not None:
        post_filters.append("metadata->>'service_status' = :service_status")
        bind["service_status"] = service_status

    where_clause = ("WHERE " + " AND ".join(post_filters)) if post_filters else ""

    sql = text(f"""
        SELECT service_id, embedding_text, metadata, similarity
        FROM (
            SELECT
                service_id, embedding_text, metadata,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE row_kind = :row_kind
              AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) candidates
        {where_clause}
        ORDER BY similarity DESC
        LIMIT :top_k
    """)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
