"""BM25 Search Tool — ParadeDB BM25 전문 검색 (인라인 토큰 + score 함수 회피).

pg_search 0.23.4 환경의 검증된 제약
------------------------------------
- ✅ 리터럴 `col @@@ '테니스장'` (인라인) + ORDER BY 없이 LIMIT
- ❌ Bind 파라미터 `col @@@ $1`
- ❌ `paradedb.score(id)` SELECT 또는 ORDER BY 사용 (service_name 컬럼에서 깨짐)
- ❌ `paradedb.boolean(...)` / `paradedb.parse(...)` API 호출
- ❌ 서로 다른 컬럼 결합 (`service_name @@@ ... OR metadata @@@ ...`)

→ 전략: **인라인 토큰 + score 함수 미사용 + ROW_NUMBER 로 rank 부여**
   ParadeDB 가 BM25 자연 순서로 결과를 반환하므로 SELECT 에 ROW_NUMBER 만
   추가하면 BM25 rank 를 얻을 수 있다. RRF 는 rank 만 필요하므로 충분.
   호환성: bm25_score = 1.0 / rank 로 환산하여 다운스트림 코드 무변경.

SQL Injection 방지 (인라인 안전성)
----------------------------------
1. Tantivy 특수문자 제거 (`+-:?\\*~^(){}[]"`)
2. **Strict 화이트리스트**: Hangul(가-힣) + alphanumeric 만 통과.
   single quote, semicolon, 공백 등 SQL meta 문자 전부 제거.
3. 컬럼명·LIMIT 은 코드 하드코딩 → 외부 입력 미수용.
4. 통과된 토큰만 `'{token}'` 형태로 SQL 에 인라인.

FastAPI Depends 미사용 — Agent 에서 직접 호출되는 내부 도구.
"""

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

BM25_LIMIT: int = 50

# DoS / SQL 안전성: 인라인 SQL 의 토큰 길이 상한. kiwipiepy 형태소가 보통 1-10자라
# 64 면 충분. 64 자 초과는 잘라낸다.
_BM25_MAX_TOKEN_LEN: int = 64

# 컬럼당 동시 실행 토큰 상한. 너무 많은 토큰이 들어오면 DB 부하 위험.
_BM25_MAX_TOKENS: int = 8

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

# Strict 화이트리스트 — SQL 인라인 안전 문자.
# 한글(가-힣, ㄱ-ㅎ, ㅏ-ㅣ) + ASCII alphanumeric. 그 외 모두 제거.
# single quote / semicolon / 공백 등 SQL meta 문자 일체 거부.
_BM25_INLINE_SAFE: re.Pattern[str] = re.compile(
    r"[^가-힣ㄱ-ㅎㅏ-ㅣa-zA-Z0-9]"
)

# 검색 대상 컬럼 — 코드 하드코딩, 외부 입력 받지 않음.
_BM25_COLUMNS: tuple[str, ...] = ("service_name", "metadata")


def _sanitize_tokens(tokens: list[str]) -> list[str]:
    """토큰을 BM25 검색 안전 형태로 정제한다.

    1단계: Tantivy 특수문자 제거 (의도치 않은 쿼리 동작 방지)
    2단계: 화이트리스트 정제 (Hangul + alphanumeric 만 허용) — SQL 인라인 안전성 확보
    3단계: 길이 상한 적용 (DoS 방지)
    4단계: 예약어 필터링
    """
    safe = []
    for t in tokens:
        t = _BM25_SPECIAL.sub("", t)
        t = _BM25_INLINE_SAFE.sub("", t)
        if len(t) > _BM25_MAX_TOKEN_LEN:
            t = t[:_BM25_MAX_TOKEN_LEN]
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


async def _search_one(
    column: str,
    token: str,
    session: AsyncSession,
    *,
    limit: int,
) -> list[dict]:
    """단일 (컬럼, 토큰) 조합 BM25 검색.

    ParadeDB 0.23.4 가 asyncpg prepared statement 의 `@@@ $N` 형태와
    `paradedb.score(id)` 함수 (service_name 컬럼 대상) 를 지원하지 않는다.

    회피 전략:
    - 토큰을 SQL 에 직접 인라인 (no bind).
    - `paradedb.score` 미사용. ParadeDB 가 BM25 relevance 순으로 결과를 반환하므로
      `ROW_NUMBER() OVER ()` 로 rank 를 부여하고 `bm25_score = 1.0 / rank` 로 환산.

    안전성 가드 (defense-in-depth):
    - 빈 token → 빈 결과 반환 (`WHERE col @@@ ''` 발급 방지).
    - limit 은 max(1, int(limit)) 로 보정 (음수/0 → 의도 외 SQL 방지).
    - token 은 사전에 _sanitize_tokens 통과 (Hangul + alphanumeric only).
    - column 은 _BM25_COLUMNS 하드코딩 값 (외부 입력 아님).

    Returns
    -------
    list[dict]
        service_id, service_name, bm25_score(=1.0/rank), bm25_rank 키 포함.
    """
    if not token:
        return []
    limit_int = max(1, int(limit))
    sql = text(f"""
        SELECT
            service_id,
            service_name,
            ROW_NUMBER() OVER () AS bm25_rank
        FROM service_embeddings
        WHERE {column} @@@ '{token}'
        LIMIT {limit_int}
    """)
    result = await session.execute(sql)
    keys = result.keys()
    rows = [dict(zip(keys, row)) for row in result.fetchall()]
    # ROW_NUMBER() returns int; convert to bm25_score for downstream RRF compatibility.
    for r in rows:
        rank = int(r["bm25_rank"])
        r["bm25_score"] = 1.0 / rank if rank > 0 else 0.0
    return rows


def _merge_by_max_score(rows_list: list[list[dict]]) -> list[dict]:
    """service_id 기준 MAX(bm25_score) 로 병합 후 결정적 정렬.

    정렬 키:
      1) bm25_score 내림차순 (높은 점수 우선)
      2) service_id 오름차순 (점수 동률 시 결정적 tie-break)
    """
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
    return sorted(
        best.values(),
        key=lambda r: (-r["bm25_score"], r["service_id"]),
    )


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

    # DoS 방지 — 토큰 상한 적용 (앞쪽 토큰 우선).
    safe = safe[:_BM25_MAX_TOKENS]

    # 각 (컬럼, 토큰) 조합을 단일 token 쿼리로 실행 후 머지.
    # asyncpg 단일 세션 제약으로 순차 실행 (vector_agent.py 와 동일 패턴).
    # 토큰 상한 덕분에 최대 C × T = 2 × 8 = 16 쿼리로 제한.
    all_rows: list[list[dict]] = []
    for column in _BM25_COLUMNS:
        for token in safe:
            rows = await _search_one(column, token, session, limit=limit)
            all_rows.append(rows)

    merged = _merge_by_max_score(all_rows)
    return merged[:limit]
