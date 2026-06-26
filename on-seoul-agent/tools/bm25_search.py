"""BM25 Search Tool — ParadeDB BM25 전문 검색 (인라인 토큰 + relevance 정렬).

pg_search 0.23.4 환경의 검증된 제약 (EXPLAIN ANALYZE 실측 갱신)
--------------------------------------------------------------
- ✅ 리터럴 `col @@@ '테니스장'` (인라인)
- ✅ `paradedb.score(id)` SELECT 및 `ORDER BY paradedb.score(id) DESC`.
  과거 docstring 은 service_name 컬럼에서 "깨진다"고 명시했으나, ParadeDB 0.23.4
  실측 결과 service_name 컬럼에서 동작한다. EXPLAIN ANALYZE 상 `Scores: true` +
  `idx_service_embeddings_bm25` Custom Scan 적중을 확인했고, 지연은 토큰당 ~30ms 다
  (테니스장 421건: 29.7ms). 과거에 보고된 ~263ms 슬로우다운은 재현되지 않았다.
- ❌ Bind 파라미터 `col @@@ $1`
- ❌ `paradedb.boolean(...)` / `paradedb.parse(...)` API 호출
- ❌ `metadata @@@ '평문토큰'` — metadata 는 bm25 인덱스에 json_fields 로 선언되어
  평문 토큰이 전 토큰에서 0건 매칭한다(라이브 EXPLAIN 실측). field-qualified
  (`metadata @@@ 'extracted.summary:토큰'`)만 매칭하나, 봉인 평가셋에서 semantic
  recall 을 떨어뜨려(0.800→0.700, summary 텍스트 IDF 오염) 채택하지 않는다.
  → BM25 검색 대상은 service_name 단일 컬럼 (_BM25_COLUMNS 주석 참조).
- ⚠️ `WHERE {col} @@@ '...'` 에는 **반드시 `AND row_kind = 'identity'`** 를 붙인다.
  bm25 인덱스가 `WHERE row_kind = 'identity'` partial 인덱스라, 이 조건이 없으면
  planner 가 partial 인덱스를 후보에서 제외해 Parallel Seq Scan(토큰당 ~350ms)으로
  떨어진다. 조건을 붙이면 ParadeDB Custom Scan(토큰당 ~30ms). row_kind 는 pg_search
  인덱스 필드가 아니라 @@@ 문법 안에 넣을 수 없고, partial 인덱스 predicate 매칭으로
  해소한다. 결과 동등(인덱스가 identity row만 색인 → @@@ 는 이미 identity만 매칭).

→ 전략: **인라인 토큰 + `ORDER BY paradedb.score(id) DESC` 로 relevance top-N 보장**
   ORDER BY 가 없으면 LIMIT 50 이 잘라내는 50건이 물리 스캔 순서(Seq vs Custom Scan)
   에 따라 달라져 비결정적이었다(테니스장 421건에서 relevance top-50 중 29건 누락 관측).
   `ORDER BY paradedb.score(id) DESC, service_id ASC` 로 (1) relevance top-N 정확성과
   (2) 점수 동률 시 service_id 결정적 tie-break 를 동시에 확보한다.
   rank 는 정렬된 결과에 `ROW_NUMBER() OVER (ORDER BY paradedb.score(id) DESC,
   service_id ASC)` 로 부여 → bm25_score = 1.0 / rank 환산. RRF 는 rank 만 사용하므로
   relevance 순 rank 와 정합적이고 다운스트림 코드는 무변경.

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
_BM25_INLINE_SAFE: re.Pattern[str] = re.compile(r"[^가-힣ㄱ-ㅎㅏ-ㅣa-zA-Z0-9]")

# 검색 대상 컬럼 — 코드 하드코딩, 외부 입력 받지 않음.
# metadata 컬럼은 의도적으로 제외한다. bm25 인덱스가 metadata 를 json_fields 로
# 선언해(idx_service_embeddings_bm25), 평문 토큰 `metadata @@@ '테니스장'` 은 전 토큰에서
# 0건 매칭한다(라이브 EXPLAIN 실측). field-qualified(`metadata @@@ 'extracted.summary:테니스장'`)
# 만 매칭하는데, 봉인 평가셋 측정 결과 그 방향은 semantic recall@10 을 0.800→0.700 으로
# 떨어뜨렸다(extracted.summary 텍스트의 도메인 공통어가 RRF 를 오염 — DDL 이 summary 를
# BM25 색인에서 제외한 IDF 오염 회피 논리와 동일). metadata 채널 제거 시 recall 동등
# (R@1 0.571 / R@5·10 0.857 / MRR 0.663 / identification 1.0 유지) + bm25 지연 절반
# (mean 413ms→197ms). 따라서 service_name 단일 채널만 유지한다.
_BM25_COLUMNS: tuple[str, ...] = ("service_name",)


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

    ParadeDB 0.23.4 가 asyncpg prepared statement 의 `@@@ $N` 형태를 지원하지 않으므로
    토큰을 SQL 에 직접 인라인한다 (no bind).

    relevance top-N 전략:
    - `ORDER BY paradedb.score(id) DESC, service_id ASC` 로 BM25 relevance 순 정렬.
      ORDER BY 가 없으면 LIMIT 컷이 물리 스캔 순서를 따라 비결정적이 되고, 매칭 > LIMIT
      인 고빈도 토큰에서 relevance 상위 행이 누락된다(실측: 테니스장 421건에서 29건 누락).
    - 정렬된 결과에 `ROW_NUMBER() OVER (... 동일 ORDER BY ...)` 로 1-based rank 부여 후
      `bm25_score = 1.0 / rank` 로 환산. RRF 는 rank 만 사용하므로 정합적·다운스트림 무변경.

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
    # `AND row_kind = 'identity'` 는 partial bm25 인덱스
    # (idx_service_embeddings_bm25 ... WHERE row_kind = 'identity') predicate 와
    # 정렬하기 위한 필수 조건이다. 누락 시 planner 가 partial 인덱스를 후보에서
    # 제외해 Parallel Seq Scan(토큰당 ~350ms)으로 떨어진다. 이 조건이 들어가면
    # ParadeDB Custom Scan(idx_service_embeddings_bm25)을 타고 토큰당 ~30ms로 떨어진다.
    # 결과 동등: bm25 인덱스가 identity row만 색인하므로 `@@@` 는 이미 identity
    # row만 매칭한다(EXPLAIN으로 확인). 따라서 이 조건은 성능만 개선하고 결과를
    # 바꾸지 않는다. ParadeDB가 row_kind를 인덱스 필드로 갖지 않아 @@@ 문법 안에는
    # 넣을 수 없고(boolean/term API 거부), partial 인덱스 predicate 매칭으로 해소된다.
    # ORDER BY paradedb.score(id) DESC: BM25 relevance 순으로 정렬해 LIMIT 컷을
    # 결정적·고관련도 우선으로 만든다 (ParadeDB 0.23.4 service_name 컬럼에서 동작,
    # idx_service_embeddings_bm25 Custom Scan 적중 — EXPLAIN ANALYZE 확인).
    # service_id ASC 는 점수 동률 시 결정적 tie-break.
    # ROW_NUMBER 는 동일 ORDER BY 윈도우로 1-based relevance rank 를 부여한다.
    sql = text(f"""
        SELECT
            service_id,
            service_name,
            ROW_NUMBER() OVER (
                ORDER BY paradedb.score(id) DESC, service_id ASC
            ) AS bm25_rank
        FROM service_embeddings
        WHERE {column} @@@ '{token}'
          AND row_kind = 'identity'
        ORDER BY paradedb.score(id) DESC, service_id ASC
        LIMIT {limit_int}
    """)
    result = await session.execute(sql)
    keys = result.keys()
    rows = [dict(zip(keys, row)) for row in result.fetchall()]
    # ROW_NUMBER() 는 int 를 반환 — 다운스트림 RRF 호환을 위해 bm25_score 로 환산.
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
    """ParadeDB BM25 전문 검색 — service_name 컬럼 (토큰별 머지).

    검색 대상은 service_name 단일 컬럼이다. metadata 컬럼은 json_fields 색인이라
    평문 토큰이 매칭되지 않고, field-qualified 방향은 recall 을 떨어뜨려 제외했다
    (_BM25_COLUMNS 주석 참조). 토큰별로 독립 실행하고 service_id 기준
    MAX(bm25_score) 로 머지한다.

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
    # 토큰 상한 덕분에 최대 C × T = 1 × 8 = 8 쿼리로 제한.
    all_rows: list[list[dict]] = []
    for column in _BM25_COLUMNS:
        for token in safe:
            rows = await _search_one(column, token, session, limit=limit)
            all_rows.append(rows)

    merged = _merge_by_max_score(all_rows)
    return merged[:limit]
