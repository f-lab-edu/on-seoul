"""BM25 Search Tool — ParadeDB BM25 전문 검색.

ParadeDB 멀티 필드 검색 전략:
  - `col @@@ parse('field', query)` 2인자 형태는 단일 토큰에서 불안정.
  - 필드 스코프를 쿼리 문자열에 직접 포함하는 1인자 형태를 사용한다:
      paradedb.parse('service_name:(토큰1 OR 토큰2)')
      paradedb.parse('metadata:(토큰1 OR 토큰2)')
  - 단일 토큰: 'service_name:토큰'  (괄호 생략)
  - 복수 토큰: 'service_name:(tok1 OR tok2 OR ...)'

SQL Injection 방지:
  - 토큰은 Python 사이드에서 Tantivy 특수문자·예약어를 제거한 뒤
    필드-스코프 쿼리 문자열로 조립되어 bind 파라미터로 전달된다.
  - 필드명(service_name, metadata)은 코드에 하드코딩되어 외부 입력이 아니다.

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


def _sanitize_tokens(tokens: list[str]) -> list[str]:
    """토큰에서 Tantivy 특수문자·예약어를 제거하고 유효 토큰만 반환한다."""
    safe = []
    for t in tokens:
        t = _BM25_SPECIAL.sub("", t)
        if t and t.upper() not in _BM25_RESERVED:
            safe.append(t)
    return safe


def build_bm25_query(tokens: list[str]) -> str:
    """토큰 배열을 ParadeDB BM25 쿼리 문자열로 변환한다 (하위 호환 유지).

    반환값은 'token1 OR token2' 형태의 raw 토큰 문자열.
    실제 필드-스코프 조립은 build_field_query() 가 담당한다.

    Returns
    -------
    str
        OR 구분 토큰 문자열. 유효 토큰이 없으면 빈 문자열.
    """
    safe = _sanitize_tokens(tokens)
    if not safe:
        return ""
    if len(safe) == 1:
        return safe[0]
    return " OR ".join(safe)


def build_field_queries(tokens: list[str]) -> tuple[str, str] | None:
    """토큰 배열을 필드-스코프 paradedb.parse 인자 문자열 쌍으로 변환한다.

    ParadeDB 1인자 parse 형식:
      단일 토큰 → 'service_name:토큰'
      복수 토큰 → 'service_name:(tok1 OR tok2 OR ...)'

    Returns
    -------
    tuple[str, str] | None
        (service_name 쿼리, metadata 쿼리). 유효 토큰 없으면 None.
    """
    safe = _sanitize_tokens(tokens)
    if not safe:
        return None

    if len(safe) == 1:
        body = safe[0]
    else:
        body = f"({' OR '.join(safe)})"

    return f"service_name:{body}", f"metadata:{body}"


async def bm25_search(
    tokens: list[str],
    session: AsyncSession,
    *,
    limit: int = BM25_LIMIT,
) -> list[dict]:
    """ParadeDB BM25 전문 검색을 수행한다.

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
        service_id, service_name, bm25_score 키를 가진 딕셔너리 리스트.
        토큰이 비어 있거나 결과가 없으면 빈 리스트.
    """
    if not tokens:
        return []

    field_queries = build_field_queries(tokens)
    if field_queries is None:
        # 모든 토큰이 특수문자/예약어로 제거된 경우 DB 호출을 생략한다.
        return []

    query_sn, query_md = field_queries

    # 필드-스코프 1인자 parse 형태: 단일/복수 토큰 모두 안정적으로 동작.
    # 2인자 parse('field', :query) 는 단일 토큰에서 "Unsupported query shape" 오류 발생.
    sql = text("""
        SELECT
            service_id,
            service_name,
            paradedb.score(id) AS bm25_score
        FROM service_embeddings
        WHERE id @@@ paradedb.boolean(
            should => ARRAY[
                paradedb.parse(:query_sn),
                paradedb.parse(:query_md)
            ]
        )
        ORDER BY paradedb.score(id) DESC
        LIMIT :limit
    """)

    result = await session.execute(
        sql, {"query_sn": query_sn, "query_md": query_md, "limit": limit}
    )
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]
