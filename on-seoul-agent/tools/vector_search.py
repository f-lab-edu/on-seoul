"""Vector Search Tool — row_kind 파라미터 기반 트랙별 검색.

Phase RRF 검색 전략:
  - row_kind='identity' : 시설 기본 정보 트랙 (Track A). post-filter 적용.
  - row_kind='summary'  : 상세 요약 트랙 (Track B). post-filter 미적용.
  - row_kind='question' : FAQ 질문 트랙 (Track C) — question_search 도구 사용.

각 트랙은 독립 쿼리로 실행된 후 RRF로 결합된다.
scan_k는 settings.rrf_scan_k_per_track 을 사용한다.

HNSW 파라미터 기준:
  - m=16, ef_construction=64: 소규모 데이터 기본값.
  - ef_search: settings.hnsw_ef_search (SET LOCAL). scan_k 후보를 채워
    exact KNN 과 동일한 recall 을 보장하기 위해 ef_search >= scan_k 로 둔다.

recall 보존 우선 결정:
  min_similarity 를 outer 로 옮기면 planner 가 HNSW Index Scan 을 택할 수 있으나,
  데이터 규모(트랙당 ~3.4k row)에서는 recall 동등(ef_search>=60)을 만족시키면
  planner 가 비용상 Seq Scan 을 택한다(실측). 즉 이 규모에서 exact Seq Scan 이
  정답이며, threshold 의 outer 이동은 SQL 레벨 결과를 바꾸지 않는다(exact 동등).
  데이터가 커지면 동일 쿼리로 HNSW 가 자연히 선택된다.
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
    area_name: list[str] | None = None,
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
        area_name 은 자치구 리스트로 metadata->>'area_name' = ANY(:areas) 매칭.
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
    if area_name:
        # 다중 지역: metadata->>'area_name' = ANY(:areas). 값은 리스트째 bind(인젝션 방지).
        post_filters.append("metadata->>'area_name' = ANY(:areas)")
        bind["areas"] = list(area_name)
    if service_status is not None:
        post_filters.append("metadata->>'service_status' = :service_status")
        bind["service_status"] = service_status

    # min_similarity 는 항상 outer 필터로 둔다. inner 서브쿼리에 두면 planner 가
    # HNSW ANN(ORDER BY embedding<=>q LIMIT)을 포기하고 Seq Scan + Sort 로 떨어진다.
    # scan_k(>=top_k) 가 충분히 커서 가장 가까운 후보 안에 threshold 통과분이 모두
    # 포함되므로 recall 동등. post_filters 가 비어도 similarity 조건은 항상 포함.
    outer_conditions = ["candidates.similarity >= :min_similarity", *post_filters]
    where_clause = "WHERE " + " AND ".join(outer_conditions)

    sql = text(f"""
        SELECT service_id, embedding_text, metadata, similarity
        FROM (
            SELECT
                service_id, embedding_text, metadata,
                1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE row_kind = :row_kind
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) candidates
        {where_clause}
        ORDER BY similarity DESC
        LIMIT :top_k
    """)

    # HNSW 후보 LIMIT(scan_k)을 실제로 채우려면 ef_search >= scan_k 여야 한다.
    # SET LOCAL 은 현재 트랜잭션에만 적용되어 커넥션 풀에 누수되지 않는다.
    # SET 은 bind 파라미터를 받지 못하므로 신뢰된 config int 를 직접 삽입한다.
    await session.execute(
        text(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    )

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
