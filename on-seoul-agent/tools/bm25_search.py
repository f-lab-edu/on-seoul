"""BM25 Search Tool — ParadeDB BM25 전문 검색.

토큰 배열을 공백으로 연결한 쿼리 문자열을 구성하고,
ParadeDB의 @@@ 연산자로 service_embeddings 테이블을 검색한다.

SQL Injection 방지: 쿼리 문자열은 bind 파라미터(:query)로만 전달된다.

FastAPI Depends로 주입되지 않고 Agent에서 직접 호출되는 내부 도구다.
"""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BM25_LIMIT: int = 50

# ParadeDB(Tantivy) 쿼리 파서가 특수하게 해석하는 문자.
# 토큰에 포함되면 접두사 검색·구문 검색·퍼지·필수/제외·필드 한정 등
# 의도치 않은 동작이 발생한다.
# +  : 필수 조건 (+term)
# -  : 제외 조건 (-term)
# :  : 필드 한정 쿼리 (field:value)
# \  : 이스케이프 문자
# ?  : 단일 문자 와일드카드
# *~^"(){}[] : 접두사·퍼지·부스팅·구문·그룹 검색
_BM25_SPECIAL: re.Pattern[str] = re.compile(r'[+\-:?\\*~^(){}\[\]"]')

# Tantivy 논리 연산 예약어.
# 토큰으로 그대로 전달되면 AND·OR·NOT 등 논리 검색으로 해석되어 결과가 왜곡된다.
_BM25_RESERVED: frozenset[str] = frozenset({"AND", "OR", "NOT", "TO", "IN"})


def build_bm25_query(tokens: list[str]) -> str:
    """토큰 배열을 ParadeDB BM25 쿼리 문자열로 변환한다.

    ParadeDB @@@: 공백으로 구분된 토큰을 OR 매칭한다.
    각 토큰에서 Tantivy 특수문자를 제거하고 예약어를 필터링하여
    의도치 않은 쿼리 동작을 방지한다.

    Parameters
    ----------
    tokens:
        형태소 분석된 토큰 리스트.

    Returns
    -------
    str
        공백 구분 토큰 문자열. 빈 리스트이거나 안전한 토큰이 없으면 빈 문자열.
    """
    safe = []
    for t in tokens:
        t = _BM25_SPECIAL.sub("", t)
        if t and t.upper() not in _BM25_RESERVED:
            safe.append(t)
    return " ".join(safe)


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
    if not query_str:
        # 모든 토큰이 특수문자/예약어로 제거된 경우 DB 호출을 생략한다.
        return []

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
