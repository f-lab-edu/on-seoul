"""BM25 Search Tool — ParadeDB BM25 전문 검색.

토큰 배열을 공백으로 연결한 쿼리 문자열을 구성하고,
ParadeDB의 @@@ 연산자로 service_embeddings 테이블을 검색한다.

SQL Injection 방지: 쿼리 문자열은 bind 파라미터(:query)로만 전달된다.

FastAPI Depends로 주입되지 않고 Agent에서 직접 호출되는 내부 도구다.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BM25_LIMIT: int = 50


def build_bm25_query(tokens: list[str]) -> str:
    """토큰 배열을 ParadeDB BM25 쿼리 문자열로 변환한다.

    ParadeDB @@@: 공백으로 구분된 토큰을 OR 매칭한다.

    Parameters
    ----------
    tokens:
        형태소 분석된 토큰 리스트.

    Returns
    -------
    str
        공백 구분 토큰 문자열. 빈 리스트이면 빈 문자열.
    """
    return " ".join(tokens)


async def bm25_search(
    tokens: list[str],
    session: AsyncSession,
    *,
    limit: int = BM25_LIMIT,
) -> list[dict]:
    """ParadeDB BM25 전문 검색을 수행한다.

    FastAPI Depends로 주입되지 않고 Agent에서 직접 호출되는 내부 도구다.

    Parameters
    ----------
    tokens:
        tokenize_query()로 생성된 형태소 토큰 리스트.
    session:
        on_ai_app 계정 AsyncSession (service_embeddings CRUD 권한).
    limit:
        반환할 최대 결과 수. 기본값: 50.

    Returns
    -------
    list[dict]
        service_id, bm25_score 키를 가진 딕셔너리 리스트.
        토큰이 비어 있거나 결과가 없으면 빈 리스트.
    """
    if not tokens:
        return []

    query_str = build_bm25_query(tokens)

    sql = text("""
        SELECT
            service_id,
            service_name,
            paradedb.score(id) AS bm25_score
        FROM service_embeddings
        WHERE service_name @@@ :query OR metadata @@@ :query
        ORDER BY paradedb.score(id) DESC
        LIMIT :limit
    """)

    result = await session.execute(sql, {"query": query_str, "limit": limit})
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
