"""안 B 스파이크 — 4채널 retrieval 단일 UNION ALL 통합문 실행 가능성 검증.

이 스크립트는 **일회성 검증 도구**다. 운영 tools/·agents/ 는 건드리지 않는다.

배경
----
현재 VectorAgent 는 4채널(Track A/B/C identity·summary·question 벡터 + BM25)을
각각 별도 쿼리·세션으로 실행한 뒤 앱에서 core/rrf.reciprocal_rank_fusion 으로
결합한다(4 라운드트립). 안 B 는 이 4채널을 **단일 UNION ALL 문장**으로 묶어
라운드트립을 4→1 로 줄이려는 설계다. 단:

  - 각 트랙의 top-k·post-filter·DISTINCT ON 을 보존한다.
  - SQL 안에서 RRF 까지 하지 않는다(앱의 reciprocal_rank_fusion 그대로 호출).
  - 결과는 (channel, service_id, rank) 형태로 반환해 앱이 채널별 순위를
    식별하고 기존 RRF·관측·가중치 유연성을 유지할 수 있어야 한다.

검증 항목 (우선순위 순)
-----------------------
1. [최우선] ParadeDB BM25 `@@@` 가 UNION ALL 안에서 합쳐지는가.
   실패하면 안 B 불가 → 안 A(벡터 3트랙 1문장 + BM25 별도, 2 I/O) 폴백 판정.
2. 실행 성공 + 결과 정합성. 통합문의 채널별 (service_id, rank) 리스트가
   기존 4개 개별 쿼리 결과와 동일한지 대조.
3. EXPLAIN ANALYZE — HNSW/BM25 인덱스 적중 여부 + 서브쿼리별 시간(4초 원인 진단).
4. asyncpg 파라미터 함정 — query_vector 반복 바인딩, optional post-filter 절 생략,
   statement_cache_size 영향.

쿼리 벡터 소스
--------------
임베딩 API(LLM) 의존을 피하려고, 기본은 DB 의 기존 identity row embedding 1건을
그대로 query_vector 로 사용한다(HNSW 경로를 실제 768-dim 벡터로 자극). 따라서
이 스크립트는 DB 만 있으면 LLM 키 없이 실행된다.
  --query "텍스트" 를 주면 실 embedder(get_embeddings)로 임베딩한다(LLM 키 필요).

사용법
------
  uv run python scripts/eval/validate_union_query.py
  uv run python scripts/eval/validate_union_query.py --bm25 "테니스장 강남"
  uv run python scripts/eval/validate_union_query.py --query "강남구 테니스장 예약" --explain
"""

import argparse
import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from core import database as _database  # noqa: E402
from core.config import settings  # noqa: E402
from core.database import ai_session_ctx  # noqa: E402
from tools.bm25_search import (  # noqa: E402
    BM25_LIMIT,
    _BM25_COLUMNS,
    _sanitize_tokens,
)
from tools.tokenizer import tokenize_query  # noqa: E402

# vector_agent 의 BM25 stopword(docs 동기) — 운영 import 회피 위해 로컬 복제.
_BM25_STOPWORDS: frozenset[str] = frozenset(
    {
        "예약",
        "서울",
        "서울시",
        "공공",
        "서비스",
        "공공서비스",
        "접수",
        "신청",
        "이용",
        "안내",
        "시설",
        "프로그램",
    }
)


# ---------------------------------------------------------------------------
# 통합문 SQL 빌더 — 기존 쿼리를 그대로 토대로 한 UNION ALL.
# ---------------------------------------------------------------------------


def build_union_sql(
    *,
    include_bm25: bool,
    bm25_terms: list[str],
    max_class_name: str | None,
    area_name: str | None,
    service_status: str | None,
) -> tuple[str, dict]:
    """4채널 단일 UNION ALL 통합문 + bind dict 를 반환한다.

    각 채널 브랜치는 (channel, service_id, rank, score) 4 컬럼으로 정규화한다.
    - 벡터 3트랙: ROW_NUMBER() OVER (ORDER BY similarity DESC) AS rank.
    - BM25: ParadeDB 자연순서를 ROW_NUMBER() OVER () 로 rank 부여(bm25_search 와 동일).

    벡터 트랙은 기존 tools/vector_search.py·question_search.py 의 서브쿼리를
    그대로 가져와 바깥에 ROW_NUMBER 만 씌운다. post-filter 절은 None 이면 생략한다
    (asyncpg AmbiguousParameterError 회피 패턴 유지).

    커밋된 Fix 1/Fix 2 쿼리 형태와 동형(공정 비교 조건):
    - Fix 1(벡터): min_similarity 를 inner WHERE 에서 빼고 outer 필터로 둔다.
      inner 는 `WHERE row_kind=X ORDER BY embedding<=>q LIMIT scan_k` 뿐.
      ef_search 는 실행 직전 SET LOCAL 로 발급(이 빌더는 SQL 만 생성).
    - Fix 2(BM25): 각 (컬럼,토큰) 브랜치에 `AND row_kind='identity'` 포함
      (partial bm25 인덱스 적중).
    """
    bind: dict = {
        "query_vector": None,  # 호출부에서 채운다(str(vector))
        "min_similarity_identity": settings.vector_min_similarity_identity,
        "min_similarity_summary": settings.vector_min_similarity_summary,
        "min_similarity_question": settings.vector_min_similarity_question,
        "scan_k": settings.rrf_scan_k_per_track,
        "top_k": settings.vector_track_top_k,
    }

    # Track A post-filter — None 이면 절 생략. min_similarity 와 함께 outer 필터로 둔다
    # (Fix 1: inner 서브쿼리에 두면 HNSW ANN 을 포기하고 Seq Scan 으로 떨어짐).
    a_filters: list[str] = []
    if max_class_name is not None:
        a_filters.append("metadata->>'max_class_name' = :max_class_name")
        bind["max_class_name"] = max_class_name
    if area_name is not None:
        a_filters.append("metadata->>'area_name' = :area_name")
        bind["area_name"] = area_name
    if service_status is not None:
        a_filters.append("metadata->>'service_status' = :service_status")
        bind["service_status"] = service_status

    # ---- Track A (identity + post-filter) — Fix 1: min_similarity outer ----
    a_outer = ["a_candidates.similarity >= :min_similarity_identity", *a_filters]
    a_where = "WHERE " + " AND ".join(a_outer)
    track_a = f"""
        SELECT 'track_a' AS channel, service_id,
               ROW_NUMBER() OVER (ORDER BY similarity DESC) AS rank,
               similarity AS score
        FROM (
            SELECT service_id, metadata,
                   1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE row_kind = 'identity'
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) a_candidates
        {a_where}
        ORDER BY similarity DESC
        LIMIT :top_k
    """

    # ---- Track B (summary, post-filter 미적용) — Fix 1: min_similarity outer ----
    track_b = """
        SELECT 'track_b' AS channel, service_id,
               ROW_NUMBER() OVER (ORDER BY similarity DESC) AS rank,
               similarity AS score
        FROM (
            SELECT service_id,
                   1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM service_embeddings
            WHERE row_kind = 'summary'
            ORDER BY embedding <=> CAST(:query_vector AS vector)
            LIMIT :scan_k
        ) b_candidates
        WHERE b_candidates.similarity >= :min_similarity_summary
        ORDER BY similarity DESC
        LIMIT :top_k
    """

    # ---- Track C (question, DISTINCT ON dedup) — Fix 1: min_similarity outer ----
    track_c = """
        SELECT 'track_c' AS channel, service_id,
               ROW_NUMBER() OVER (ORDER BY similarity DESC) AS rank,
               similarity AS score
        FROM (
            SELECT * FROM (
                SELECT DISTINCT ON (service_id)
                    service_id,
                    1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
                FROM service_embeddings
                WHERE row_kind = 'question'
                ORDER BY service_id, embedding <=> CAST(:query_vector AS vector)
            ) ranked
            WHERE ranked.similarity >= :min_similarity_question
            ORDER BY similarity DESC
            LIMIT :top_k
        ) c_candidates
    """

    branches = [track_a, track_b, track_c]

    # ---- BM25 (ParadeDB @@@, 인라인 토큰) ----
    # bm25_search.py 제약: bind param 불가 → 토큰을 인라인(sanitize 후).
    # OR-across-columns 불가 → 컬럼·토큰별 브랜치를 각각 UNION ALL 로 추가.
    # 검증 1의 핵심: 이 @@@ 브랜치가 벡터 브랜치들과 한 문장 UNION ALL 로 묶일 때
    # ParadeDB custom scan 이 실제로 동작하는가.
    # 채널 라벨은 (컬럼, 토큰) 단위로 부여한다. 운영 bm25_search 는
    # (컬럼, 토큰) 조합마다 독립 ROW_NUMBER() 로 rank 를 매기고 service_id 별
    # MAX(1/rank) 로 머지하므로, 토큰을 컬럼 단위로 뭉치면 per-token rank 가
    # 사라져 머지 결과가 달라진다(검증에서 실제로 mismatch 발생).
    # Fix 2: 각 (컬럼,토큰) 브랜치에 AND row_kind='identity' 를 붙여 partial bm25
    # 인덱스(WHERE row_kind='identity') 를 적중시킨다. 누락 시 Parallel Seq Scan 폴백.
    if include_bm25 and bm25_terms:
        for column in _BM25_COLUMNS:
            for t_idx, token in enumerate(bm25_terms):
                branches.append(f"""
        SELECT 'bm25::{column}::{t_idx}' AS channel, service_id,
               ROW_NUMBER() OVER () AS rank,
               NULL::float8 AS score
        FROM service_embeddings
        WHERE {column} @@@ '{token}'
          AND row_kind = 'identity'
        LIMIT {BM25_LIMIT}
                """)

    # 각 브랜치를 괄호로 감싼다. PostgreSQL 은 UNION ALL 브랜치가 자체 ORDER BY/LIMIT
    # 을 가지면 괄호로 감싸야 한다(미괄호 시 "syntax error at or near UNION").
    sql = "\nUNION ALL\n".join(f"(\n{b.strip()}\n)" for b in branches)
    return sql, bind


# ---------------------------------------------------------------------------
# 개별 4쿼리 (비교 기준) — 기존 운영 도구 호출.
# ---------------------------------------------------------------------------


async def _individual_channels(
    session: AsyncSession,
    query_vector: list[float],
    *,
    bm25_terms: list[str],
    max_class_name: str | None,
    area_name: str | None,
    service_status: str | None,
) -> dict[str, list[str]]:
    """기존 개별 도구로 채널별 service_id 순위 리스트(통합문 대조 기준)."""
    from tools.bm25_search import bm25_search
    from tools.question_search import question_search
    from tools.vector_search import vector_search

    a_rows = await vector_search(
        session,
        query_vector,
        row_kind="identity",
        max_class_name=max_class_name,
        area_name=area_name,
        service_status=service_status,
    )
    b_rows = await vector_search(session, query_vector, row_kind="summary")
    c_rows = await question_search(session, query_vector)
    d_rows = await bm25_search(bm25_terms, session) if bm25_terms else []

    return {
        "track_a": [r["service_id"] for r in a_rows],
        "track_b": [r["service_id"] for r in b_rows],
        "track_c": [r["service_id"] for r in c_rows],
        "bm25": [r["service_id"] for r in d_rows],
    }


# ---------------------------------------------------------------------------
# 통합문 실행 → 채널별 순위 리스트.
# ---------------------------------------------------------------------------


async def _run_union(
    session: AsyncSession,
    sql: str,
    bind: dict,
) -> dict[str, list[str]]:
    """통합문 실행 후 채널별 (rank 순) service_id 리스트로 집계.

    Fix 1 과 동형으로 통합문 실행 직전 SET LOCAL hnsw.ef_search 를 발급한다
    (HNSW 후보 LIMIT(scan_k)을 채워 exact KNN recall 동등 보장).
    """
    await session.execute(
        text(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}")
    )
    result = await session.execute(text(sql), bind)
    keys = result.keys()
    rows = [dict(zip(keys, row)) for row in result.fetchall()]

    by_channel: dict[str, list[tuple[int, str]]] = {}
    for r in rows:
        by_channel.setdefault(r["channel"], []).append(
            (int(r["rank"]), r["service_id"])
        )

    out: dict[str, list[str]] = {}
    for ch, pairs in by_channel.items():
        pairs.sort(key=lambda p: p[0])
        out[ch] = [sid for _, sid in pairs]
    return out


def _merge_union_bm25(union: dict[str, list[str]]) -> list[str]:
    """통합문의 컬럼별 BM25 브랜치(service_name_bm25/metadata_bm25)를 운영
    _merge_by_max_score 와 동형으로 service_id 기준 MAX(score=1/rank) 머지.

    rank 가 작을수록 score 가 크므로, service_id 별 최소 rank 를 채택하고
    (-score, service_id) 정렬 = (min_rank ASC, service_id ASC) 와 동일.
    """
    best_rank: dict[str, int] = {}
    for ch, sids in union.items():
        if not ch.startswith("bm25::"):
            continue
        for rank, sid in enumerate(sids, start=1):
            if sid not in best_rank or rank < best_rank[sid]:
                best_rank[sid] = rank
    ordered = sorted(best_rank.items(), key=lambda kv: (kv[1], kv[0]))
    return [sid for sid, _ in ordered[:BM25_LIMIT]]


# ---------------------------------------------------------------------------
# EXPLAIN ANALYZE.
# ---------------------------------------------------------------------------


async def _explain(
    session: AsyncSession, sql: str, bind: dict, *, analyze: bool
) -> str:
    mode = "EXPLAIN (ANALYZE, BUFFERS, VERBOSE)" if analyze else "EXPLAIN (VERBOSE)"
    result = await session.execute(text(f"{mode}\n{sql}"), bind)
    return "\n".join(row[0] for row in result.fetchall())


# ---------------------------------------------------------------------------
# query_vector 소스.
# ---------------------------------------------------------------------------


async def _get_query_vector(session: AsyncSession, query: str | None) -> list[float]:
    """query 가 주어지면 실 embedder 로 임베딩, 아니면 DB identity row embedding 1건."""
    if query:
        from llm.client import get_embeddings

        embedder = get_embeddings()
        return await embedder.aembed_query(query)

    result = await session.execute(
        text(
            "SELECT embedding FROM service_embeddings "
            "WHERE row_kind = 'identity' AND embedding IS NOT NULL "
            "ORDER BY service_id LIMIT 1"
        )
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError("service_embeddings 에 identity embedding 이 없습니다.")
    raw = row[0]
    # pgvector 는 보통 str("[...]") 로 돌아온다. list 면 그대로.
    if isinstance(raw, str):
        return [float(x) for x in raw.strip("[]").split(",")]
    return [float(x) for x in raw]


# ---------------------------------------------------------------------------
# 진입점.
# ---------------------------------------------------------------------------


def _compare(label: str, union_list: list[str], indiv_list: list[str]) -> bool:
    same = union_list == indiv_list
    mark = "OK " if same else "MISMATCH"
    print(f"  [{mark}] {label}: union={len(union_list)} indiv={len(indiv_list)}")
    if not same:
        print(f"        union : {union_list}")
        print(f"        indiv : {indiv_list}")
        # 집합 차이도 표시(정렬만 다른지 vs 멤버가 다른지 구분).
        if set(union_list) == set(indiv_list):
            print("        → 멤버 동일, 정렬만 상이(동점 tie-break 차이 가능)")
    return same


async def _run(args: argparse.Namespace) -> None:
    bm25_text = args.bm25 or args.query or ""
    tokens = tokenize_query(bm25_text) if bm25_text else []
    bm25_terms = _sanitize_tokens([t for t in tokens if t not in _BM25_STOPWORDS])
    bm25_terms = bm25_terms[:8]  # _BM25_MAX_TOKENS

    print("=" * 70)
    print("안 B 스파이크 — 4채널 UNION ALL 통합문 검증")
    print("=" * 70)
    print("statement_cache_size : 0 (core.database connect_args)")
    print(
        f"post-filter          : max_class={args.max_class_name} "
        f"area={args.area_name} status={args.service_status}"
    )
    print(f"BM25 terms (sanitized): {bm25_terms or '(없음 — BM25 채널 제외)'}")
    print()

    db_ok = True
    try:
        async with ai_session_ctx() as session:
            query_vector = await _get_query_vector(session, args.query)
            print(
                f"query_vector dim     : {len(query_vector)} "
                f"(source: {'embedder' if args.query else 'DB identity row'})"
            )
            print()

            # ---- 검증 1+2: 통합문 실행 + 정합성 ----
            sql, bind = build_union_sql(
                include_bm25=bool(bm25_terms),
                bm25_terms=bm25_terms,
                max_class_name=args.max_class_name,
                area_name=args.area_name,
                service_status=args.service_status,
            )
            bind["query_vector"] = str(query_vector)

            print(
                "[1] 통합문 실행 (BM25 @@@ 포함 여부: %s)"
                % ("포함" if bm25_terms else "제외")
            )
            try:
                union = await _run_union(session, sql, bind)
                print("    실행 성공.")
                bm25_branches = [c for c in union if c.startswith("bm25::")]
                print(f"    반환 채널: {sorted(union.keys())}")
                if bm25_terms:
                    if bm25_branches:
                        print(f"    → BM25 @@@ 브랜치 동작 확인: {bm25_branches}")
                    else:
                        print("    → BM25 @@@ 브랜치가 결과 0건(매칭 없음일 수 있음).")
            except Exception as e:
                print(f"    [실패] 통합문 실행 에러: {type(e).__name__}: {e}")
                if bm25_terms:
                    print(
                        "    → 검증 1 핵심: BM25 @@@ 가 UNION ALL 에서 거부됨 가능성."
                    )
                    print("    → 벡터만 통합문을 재시도해 BM25 단독 원인인지 분리한다.")
                    sql_v, bind_v = build_union_sql(
                        include_bm25=False,
                        bm25_terms=[],
                        max_class_name=args.max_class_name,
                        area_name=args.area_name,
                        service_status=args.service_status,
                    )
                    bind_v["query_vector"] = str(query_vector)
                    try:
                        await _run_union(session, sql_v, bind_v)
                        print(
                            "    → 벡터 전용 통합문은 성공 → @@@ 가 단독 원인(안 B 불가, 안 A 폴백)."
                        )
                    except Exception as e2:
                        await session.rollback()
                        print(f"    → 벡터 전용도 실패: {type(e2).__name__}: {e2}")
                await session.rollback()
                raise

            # ---- 검증 2: 개별 4쿼리 대조 ----
            print()
            print("[2] 개별 4쿼리 대조 (통합문 결과 == 기존 도구 결과여야 정상)")
            indiv = await _individual_channels(
                session,
                query_vector,
                bm25_terms=bm25_terms,
                max_class_name=args.max_class_name,
                area_name=args.area_name,
                service_status=args.service_status,
            )
            all_match = True
            all_match &= _compare("track_a", union.get("track_a", []), indiv["track_a"])
            all_match &= _compare("track_b", union.get("track_b", []), indiv["track_b"])
            all_match &= _compare("track_c", union.get("track_c", []), indiv["track_c"])
            if bm25_terms:
                union_bm25 = _merge_union_bm25(union)
                all_match &= _compare("bm25(merged)", union_bm25, indiv["bm25"])
            print(f"    → 전체 정합성: {'일치' if all_match else '불일치(상기 참조)'}")

            # ---- 검증 3: EXPLAIN ANALYZE ----
            if args.explain:
                print()
                print("[3] EXPLAIN ANALYZE — 통합문")
                plan = await _explain(session, sql, bind, analyze=True)
                print(plan)
                print()
                print("[3b] EXPLAIN ANALYZE — 개별 Track A (HNSW 적중 확인용)")
                a_sql, a_bind = build_union_sql(
                    include_bm25=False,
                    bm25_terms=[],
                    max_class_name=args.max_class_name,
                    area_name=args.area_name,
                    service_status=args.service_status,
                )
                # Track A 만 떼서 보고 싶으면 통합문 전체로 충분(브랜치별 노드가 보임).
    except Exception as e:
        db_ok = False
        # 연결 자체 실패와 쿼리 실패를 구분: 위에서 raise 된 쿼리 실패는 메시지 이미 출력.
        if "통합문" not in str(e):
            print(f"\n[DB 연결 불가] {type(e).__name__}: {str(e)[:300]}")
            print("→ 실측 미완. SQL 빌더 단위는 --print-sql 로 문법 확인 가능.")
    finally:
        await _database._on_ai_engine.dispose()
        await _database._on_data_engine.dispose()

    print()
    print("=" * 70)
    print(f"DB 실측: {'완료' if db_ok else '미완(연결 불가)'}")
    print("=" * 70)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="4채널 UNION ALL 통합문 검증 스파이크",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--query",
        default=None,
        help="실 embedder 로 임베딩할 질의(LLM 키 필요). 미지정 시 DB row embedding 사용.",
    )
    p.add_argument(
        "--bm25",
        default=None,
        help="BM25 토큰 추출용 텍스트(미지정 시 --query 사용, 둘 다 없으면 BM25 채널 제외).",
    )
    p.add_argument(
        "--max-class-name",
        dest="max_class_name",
        default=None,
        help="Track A post-filter: 대분류명",
    )
    p.add_argument(
        "--area-name",
        dest="area_name",
        default=None,
        help="Track A post-filter: 자치구",
    )
    p.add_argument(
        "--service-status",
        dest="service_status",
        default=None,
        help="Track A post-filter: 상태",
    )
    p.add_argument(
        "--explain", action="store_true", help="EXPLAIN ANALYZE 출력(검증 3)"
    )
    p.add_argument(
        "--print-sql",
        action="store_true",
        help="통합문 SQL 만 출력하고 종료(DB 미연결).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.print_sql:
        bm25_text = args.bm25 or args.query or ""
        tokens = tokenize_query(bm25_text) if bm25_text else []
        terms = _sanitize_tokens([t for t in tokens if t not in _BM25_STOPWORDS])[:8]
        sql, _ = build_union_sql(
            include_bm25=bool(terms),
            bm25_terms=terms,
            max_class_name=args.max_class_name,
            area_name=args.area_name,
            service_status=args.service_status,
        )
        print(sql)
        sys.exit(0)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
