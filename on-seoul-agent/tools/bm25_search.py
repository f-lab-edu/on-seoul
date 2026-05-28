"""BM25 Search Tool — ParadeDB BM25 전문 검색 (이중 컬럼 머지 전략).

pg_search 0.23.4 환경의 검증된 제약
------------------------------------
- ✅ `col @@@ 'X' OR col @@@ 'Y'` — 같은 컬럼에 PostgreSQL native OR 가능
- ❌ `service_name @@@ ... OR metadata @@@ ...` — 서로 다른 컬럼 결합 불가
- ❌ `UNION ALL` 로 두 컬럼 BM25 합치기 — 단일 plan 안에서 실패
- ❌ `paradedb.boolean(...)` 결과를 `@@@` 우측에 사용 — Unsupported query shape
- ❌ `service_name @@@ 'tok1 OR tok2'` — query string 안의 OR/공백 분리 실패

→ 전략: **두 번 호출 + Python 사이드 머지**
   1) service_name @@@ tok1 OR tok2 OR ... (단일 쿼리)
   2) metadata @@@ tok1 OR tok2 OR ... (단일 쿼리)
   3) service_id 기준 MAX(bm25_score) 로 병합 후 정렬·제한

SQL Injection 방지
-------------------
- 토큰은 Tantivy 특수문자·예약어 제거 후 SQLAlchemy bind 파라미터로 전달.
- 컬럼명(service_name, metadata)은 코드 하드코딩 (외부 입력 아님).
- OR 절은 토큰 수만큼 동적으로 생성하되 바인드 이름만 SQL 에 삽입.

FastAPI Depends 미사용 — Agent 에서 직접 호출되는 내부 도구.
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

# Tantivy 논리 연산 예약어. 토큰으로 전달되면 논리 검색으로 해석됨.
_BM25_RESERVED: frozenset[str] = frozenset({"AND", "OR", "NOT", "TO", "IN"})

# 검색 대상 컬럼 — 코드 하드코딩, 외부 입력 받지 않음.
_BM25_COLUMNS: tuple[str, ...] = ("service_name", "metadata")


def _sanitize_tokens(tokens: list[str]) -> list[str]:
    """토큰에서 Tantivy 특수문자·예약어를 제거하고 유효 토큰만 반환한다."""
    safe = []
    for t in tokens:
        t = _BM25_SPECIAL.sub("", t)
        if t and t.upper() not in _BM25_RESERVED:
            safe.append(t)
    return safe


def build_bm25_query(tokens: list[str]) -> str:
    """토큰 배열을 OR 구분 문자열로 변환 (하위 호환 유지).

    실제 쿼리 실행은 컬럼별 OR 절을 동적으로 구성한다 (`_build_or_clause`).

    Returns
    -------
    str
        'token1 OR token2 ...' 형태. 유효 토큰 없으면 빈 문자열.
    """
    safe = _sanitize_tokens(tokens)
    if not safe:
        return ""
    return " OR ".join(safe)


def _build_or_clause(column: str, tokens: list[str]) -> tuple[str, dict]:
    """단일 컬럼에 대해 `col @@@ :tokN OR col @@@ :tokM ...` WHERE 절을 생성한다.

    Parameters
    ----------
    column:
        BM25 인덱싱된 컬럼명 (service_name | metadata). 코드 하드코딩.
    tokens:
        sanitize 된 유효 토큰 리스트.

    Returns
    -------
    tuple[str, dict]
        (WHERE 절 문자열, 바인드 파라미터 dict).
    """
    bind_names = [f"tok_{i}" for i in range(len(tokens))]
    clause = " OR ".join(f"{column} @@@ :{name}" for name in bind_names)
    params = dict(zip(bind_names, tokens))
    return clause, params


async def _search_column(
    column: str,
    tokens: list[str],
    session: AsyncSession,
    *,
    limit: int,
) -> list[dict]:
    """단일 컬럼 BM25 검색 — 토큰 OR 결합."""
    where_clause, bind_params = _build_or_clause(column, tokens)
    sql = text(f"""
        SELECT
            service_id,
            service_name,
            paradedb.score(id) AS bm25_score
        FROM service_embeddings
        WHERE {where_clause}
        ORDER BY bm25_score DESC
        LIMIT :limit
    """)
    bind_params["limit"] = limit
    result = await session.execute(sql, bind_params)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]


def _merge_by_max_score(rows_list: list[list[dict]]) -> list[dict]:
    """service_id 기준 MAX(bm25_score) 로 병합 후 점수 내림차순 정렬."""
    best: dict[str, dict] = {}
    for rows in rows_list:
        for row in rows:
            sid = row["service_id"]
            score = float(row.get("bm25_score") or 0.0)
            prev = best.get(sid)
            if prev is None or score > float(prev["bm25_score"]):
                best[sid] = {
                    "service_id": sid,
                    "service_name": row.get("service_name"),
                    "bm25_score": score,
                }
    return sorted(best.values(), key=lambda r: r["bm25_score"], reverse=True)


async def bm25_search(
    tokens: list[str],
    session: AsyncSession,
    *,
    limit: int = BM25_LIMIT,
) -> list[dict]:
    """ParadeDB BM25 전문 검색 — service_name + metadata 두 컬럼 머지.

    pg_search 0.23.4 제약으로 단일 SQL 안에서 두 컬럼을 결합할 수 없어
    컬럼별로 독립 실행하고 service_id 기준 MAX(bm25_score) 로 머지한다.

    Parameters
    ----------
    tokens:
        tokenize_query() 로 생성된 형태소 토큰 리스트.
    session:
        on_ai_app 계정 AsyncSession (service_embeddings 권한).
    limit:
        최종 반환 최대 결과 수. 기본값: 50.

    Returns
    -------
    list[dict]
        service_id, service_name, bm25_score 키를 가진 딕셔너리 리스트.
        토큰이 비어 있거나 결과가 없으면 빈 리스트.
    """
    if not tokens:
        return []

    safe = _sanitize_tokens(tokens)
    if not safe:
        # 모든 토큰이 특수문자/예약어로 제거된 경우 DB 호출 생략.
        return []

    # 각 컬럼 BM25 인덱스를 독립적으로 조회 후 머지.
    # 각 컬럼당 limit 만큼 가져와서 합쳐도 최종 limit 로 잘라낸다.
    rows_per_column = [
        await _search_column(column, safe, session, limit=limit)
        for column in _BM25_COLUMNS
    ]

    merged = _merge_by_max_score(rows_per_column)
    return merged[:limit]
