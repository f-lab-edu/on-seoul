"""병렬 검색 도입 전(순차) vs 후(병렬) retrieval 구간 비교.

VectorAgent.search()의 4채널 retrieval(채널 팬아웃 + RRF 결합)을 두 방식으로
실행해 **retrieval 구간 지연**과 **검색 품질 동등성**을 비교한다.

- 순차(before): 4채널(Track A/B/C/BM25)을 단일 ai_session에서 순차 await.
- 병렬(after) : 채널별 독립 ai_session_ctx()를 열어 asyncio.gather로 동시 실행
  (운영 VectorAgent와 동일한 agents.vector_agent.run_parallel_channels 경로).

측정 범위 (엄격히 한정)
----------------------
**retrieval 구간만** 측정한다 = 4채널 팬아웃 + RRF 결합.
다음은 타이밍에서 **제외**한다: refine LLM 호출, 임베딩(aembed_query),
hydration(hydrate_services), 토크나이징. 이를 위해 질의마다 refined_query·
query_vector·bm25_tokens·post-filter를 **1회만** 계산해 순차/병렬 두 경로에
**동일하게 주입**한다(임베딩·refine를 두 번 돌리지 않는다).

공정 비교 장치
-------------
- 워밍업 1회: 측정 전 순차/병렬을 1회씩 실행해 버린다(PG page cache 편향 제거).
- 실행 순서 교대: 매 반복 짝수/홀수에 따라 순차/병렬 실행 순서를 swap한다.
- 동일 엔진/풀: 순차·병렬 모두 core.database 앱 글로벌 엔진(ai_session_ctx /
  data_session_ctx)을 사용한다. 스크립트 시작 시 lifespan과 동일하게
  init_global_sema()로 세마포어를 초기화하고, 종료 시 엔진을 dispose한다.

품질 동등성
----------
순차 vs 병렬은 *무엇을 계산하는지*가 아니라 *실행 타이밍*만 다르므로
recall@k(1/5/10)·MRR이 동일해야 정상이다. 추가로 질의별 top-k(rrf_top_k_final)
결과 service_id **집합** 일치 여부를 카운트한다(RRF 동점 순서 차이는 set 비교로 흡수).

주의
----
  - 실측은 실제 DB 연결이 필요하다(.env: ON_AI_DATABASE_URL / ON_DATA_DATABASE_URL).
    DB 없이도 import / --help / CLI 파싱은 동작한다.
  - eval_set_holdout.tsv 는 봉인 평가셋이다. 프롬프트·few-shot 에 사용 금지.

사용법
------
  # 전체 평가셋, 질의별 5회 반복
  uv run python scripts/eval/compare_parallel.py

  # smoke test (앞 10건, 3회 반복, 세마포어 cap 4)
  uv run python scripts/eval/compare_parallel.py --limit 10 --reps 3 --sema-cap 4

  # 출력 경로 지정
  uv run python scripts/eval/compare_parallel.py \\
      --output scripts/eval/eval_results/parallel_compare.json
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from agents.router_agent import RouterAgent
from agents.vector_agent import (
    _BM25_STOPWORDS,
    _resolve_weights,
    _safe_bm25_search,
    _safe_question_search,
    _safe_vector_search,
    run_parallel_channels,
)
from core import database as _database
from core.concurrency import init_global_sema
from core.config import settings
from core.database import ai_session_ctx
from core.rrf import reciprocal_rank_fusion
from llm.client import get_chat_model, get_embeddings
from scripts.eval.run_recall import _AT_K, EvalRow, _compute_metrics, load_holdout
from tools.tokenizer import atokenize_query

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_HOLDOUT = Path(__file__).resolve().parent / "eval_set_holdout.tsv"
_DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "eval_results"
    / f"compare_parallel_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
)


# ---------------------------------------------------------------------------
# 질의별 사전 계산 입력 — 순차/병렬에 동일하게 주입한다(임베딩·refine 1회만).
# ---------------------------------------------------------------------------


@dataclass
class _PreparedQuery:
    row: EvalRow
    refined_query: str
    query_vector: list[float]
    bm25_tokens: list[str]
    max_class_name: str | None
    area_name: str | None
    service_status: str | None
    weights: dict[str, float] | None


@dataclass
class _QueryTiming:
    query: str
    seq_median_ms: float
    par_median_ms: float
    seq_samples_ms: list[float] = field(default_factory=list)
    par_samples_ms: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RRF 결합 — 순차/병렬 공통(동일 로직이어야 품질이 동일).
# ---------------------------------------------------------------------------


def _fuse(
    a_rows: list[dict],
    b_rows: list[dict],
    c_rows: list[dict],
    d_rows: list[dict],
    weights: dict[str, float] | None,
) -> list[str]:
    """4채널 결과를 RRF 결합해 rrf_top_k_final 컷 service_id 순위 리스트 반환."""
    merged = reciprocal_rank_fusion(
        {
            "track_a": [r["service_id"] for r in a_rows],
            "track_b": [r["service_id"] for r in b_rows],
            "track_c": [r["service_id"] for r in c_rows],
            "bm25": [r["service_id"] for r in d_rows],
        },
        weights=weights,
        k_constant=settings.rrf_k_constant,
    )
    return [sid for sid, _ in merged[: settings.rrf_top_k_final]]


# ---------------------------------------------------------------------------
# 순차 경로 (병렬 도입 전) — 단일 ai_session 에서 4채널 순차 await.
# run_recall._search_vector 의 순차 패턴과 동일(try/except + rollback, 동일 RRF).
# ---------------------------------------------------------------------------


async def _retrieve_sequential(pq: _PreparedQuery) -> list[str]:
    """단일 세션에서 Track A→B→C→BM25 순차 실행 후 RRF 결합."""
    async with ai_session_ctx() as session:
        try:
            a_rows = await _safe_vector_search(
                session,
                pq.query_vector,
                row_kind="identity",
                max_class_name=pq.max_class_name,
                area_name=pq.area_name,
                service_status=pq.service_status,
            )
        except Exception:
            await session.rollback()
            a_rows = []

        try:
            b_rows = await _safe_vector_search(
                session, pq.query_vector, row_kind="summary"
            )
        except Exception:
            await session.rollback()
            b_rows = []

        try:
            c_rows = await _safe_question_search(session, pq.query_vector)
        except Exception:
            await session.rollback()
            c_rows = []

        if pq.bm25_tokens:
            try:
                d_rows = await _safe_bm25_search(session, pq.bm25_tokens)
            except Exception:
                await session.rollback()
                d_rows = []
        else:
            d_rows = []

    return _fuse(a_rows, b_rows, c_rows, d_rows, pq.weights)


# ---------------------------------------------------------------------------
# 병렬 경로 (병렬 도입 후) — 운영 VectorAgent.run_parallel_channels 재사용.
# ---------------------------------------------------------------------------


async def _retrieve_parallel(pq: _PreparedQuery) -> list[str]:
    """채널별 독립 세션 + asyncio.gather 팬아웃 후 RRF 결합(운영 경로)."""
    a_rows, b_rows, c_rows, d_rows = await run_parallel_channels(
        pq.query_vector,
        pq.bm25_tokens,
        max_class_name=pq.max_class_name,
        area_name=pq.area_name,
        service_status=pq.service_status,
    )
    return _fuse(a_rows, b_rows, c_rows, d_rows, pq.weights)


# ---------------------------------------------------------------------------
# 질의 사전 계산 — refine/classify·임베딩·토크나이징은 질의당 1회(타이밍 제외).
# ---------------------------------------------------------------------------


async def _prepare_query(
    row: EvalRow,
    *,
    embedder,
    router: RouterAgent,
    weights: dict[str, float] | None,
) -> _PreparedQuery:
    """run_recall.main()/_search_vector 와 동일한 post-filter 추출 경로.

    Router.classify 로 refined_query + post-filter(max_class_name/area_name/
    service_status)를 1회 추출하고, refined_query 를 1회 임베딩·토크나이징한다.
    """
    embed_query = row.query
    pf_max_class_name: str | None = None
    pf_area_name: str | None = None
    pf_service_status: str | None = None
    try:
        intent = await router.classify(row.query)
        embed_query = intent.refined_query or row.query
        pf_max_class_name = intent.max_class_name
        pf_area_name = intent.area_name
        pf_service_status = intent.service_status
    except Exception as e:
        logger.warning("router.classify 실패 [%s]: %s", row.query[:30], e)

    query_vector = await embedder.aembed_query(embed_query)
    tokens = await atokenize_query(embed_query)
    bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]

    return _PreparedQuery(
        row=row,
        refined_query=embed_query,
        query_vector=query_vector,
        bm25_tokens=bm25_tokens,
        max_class_name=pf_max_class_name,
        area_name=pf_area_name,
        service_status=pf_service_status,
        weights=weights,
    )


# ---------------------------------------------------------------------------
# 측정 루프
# ---------------------------------------------------------------------------


async def _measure_query(pq: _PreparedQuery, *, reps: int) -> _QueryTiming:
    """질의 1건의 retrieval 구간을 순차/병렬 각각 reps회 측정.

    - 워밍업: 순차/병렬 1회씩 실행해 버린다(page cache 편향 제거).
    - 교대: 반복 i가 짝수면 순차→병렬, 홀수면 병렬→순차 순서로 실행한다.
    """
    # 워밍업 (둘 다 1회 버림)
    await _retrieve_sequential(pq)
    await _retrieve_parallel(pq)

    seq_samples: list[float] = []
    par_samples: list[float] = []
    for i in range(reps):
        if i % 2 == 0:
            seq_samples.append(await _time_retrieval(_retrieve_sequential, pq))
            par_samples.append(await _time_retrieval(_retrieve_parallel, pq))
        else:
            par_samples.append(await _time_retrieval(_retrieve_parallel, pq))
            seq_samples.append(await _time_retrieval(_retrieve_sequential, pq))

    return _QueryTiming(
        query=pq.row.query,
        seq_median_ms=statistics.median(seq_samples),
        par_median_ms=statistics.median(par_samples),
        seq_samples_ms=[round(x, 3) for x in seq_samples],
        par_samples_ms=[round(x, 3) for x in par_samples],
    )


async def _time_retrieval(fn, pq: _PreparedQuery) -> float:
    """retrieval 함수 1회 실행 구간을 perf_counter로 측정(ms)."""
    start = time.perf_counter()
    await fn(pq)
    return (time.perf_counter() - start) * 1000.0


# ---------------------------------------------------------------------------
# 품질 동등성 비교
# ---------------------------------------------------------------------------


@dataclass
class _QualityRow:
    query: str
    seq_ids: list[str]
    par_ids: list[str]


# ---------------------------------------------------------------------------
# 리포트
# ---------------------------------------------------------------------------


def _print_report(result: dict) -> None:
    print(f"\n{'=' * 64}")
    print(
        f"병렬 검색 전/후 retrieval 비교  |  질의 {result['total_queries']}건, "
        f"reps={result['reps']}, sema_cap={result['sema_cap']}"
    )
    print(f"{'=' * 64}")

    lat = result["latency"]
    print("\n[retrieval 구간 지연] (질의별 중앙값들의 집계, ms)")
    print(f"  순차 median : {lat['seq_median_ms']:.2f}")
    print(f"  병렬 median : {lat['par_median_ms']:.2f}")
    print(f"  speedup     : {lat['speedup']:.2f}x  (순차/병렬)")
    print(f"  순차 p50/p95: {lat['seq_p50_ms']:.2f} / {lat['seq_p95_ms']:.2f}")
    print(f"  병렬 p50/p95: {lat['par_p50_ms']:.2f} / {lat['par_p95_ms']:.2f}")

    q = result["quality"]
    print("\n[품질 동등성] (순차 vs 병렬 — 동일해야 정상)")
    for k in _AT_K:
        sk = q["sequential"][f"recall@{k}"]
        pk = q["parallel"][f"recall@{k}"]
        print(f"  recall@{k:<2}: 순차 {sk:.4f} | 병렬 {pk:.4f} | delta {pk - sk:+.4f}")
    print(
        f"  MRR    : 순차 {q['sequential']['mrr']:.4f} | "
        f"병렬 {q['parallel']['mrr']:.4f} | delta {q['parallel']['mrr'] - q['sequential']['mrr']:+.4f}"
    )

    s = result["set_equivalence"]
    print("\n[top-k 결과 집합 일치]")
    print(f"  완전 일치 : {s['match']}건")
    print(f"  불일치    : {s['mismatch']}건")
    for ex in s["mismatch_examples"][:10]:
        print(f"    - {ex['query'][:50]}")
        print(f"        순차: {ex['seq_ids']}")
        print(f"        병렬: {ex['par_ids']}")

    print(f"\n{'=' * 64}\n")


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> None:
    holdout_path = Path(args.holdout)
    if not holdout_path.exists():
        print(f"오류: holdout 파일 없음 — {holdout_path}", file=sys.stderr)
        sys.exit(1)

    eval_rows = load_holdout(holdout_path)
    # 병렬/순차 비교는 retrieval 구간 비교이므로 SQL 의도도 벡터 경로로 측정한다.
    if args.limit:
        eval_rows = eval_rows[: args.limit]
    if not eval_rows:
        print("오류: 정답이 있는 질의가 없습니다.", file=sys.stderr)
        sys.exit(1)

    # weights 결정 — run_recall.main()과 동일 로직.
    # unweighted baseline이면 None(모든 채널 가중치 1.0).
    weights = _resolve_weights(settings.vector_default_sub_intent)

    # 세마포어 cap 보장 — lifespan 밖이라 None이면 병렬이 무제한이 된다.
    sema_cap = (
        args.sema_cap
        if args.sema_cap is not None
        else settings.vector_global_concurrency
    )
    init_global_sema(sema_cap)

    print(f"평가셋 로드: {len(eval_rows)}건 (from {holdout_path})")
    print(
        f"측정 시작 (reps={args.reps}, sema_cap={sema_cap}, "
        f"weights={'None (unweighted)' if weights is None else weights})"
    )

    embedder = get_embeddings()
    router = RouterAgent(model=get_chat_model())

    timings: list[_QueryTiming] = []
    quality_rows: list[_QualityRow] = []
    correct_by_query: dict[str, set[str]] = {}

    try:
        for i, row in enumerate(eval_rows, 1):
            print(f"  [{i:>3}/{len(eval_rows)}] {row.query[:48]}", end="\r")
            pq = await _prepare_query(
                row, embedder=embedder, router=router, weights=weights
            )
            timings.append(await _measure_query(pq, reps=args.reps))
            # 품질: 순차/병렬 결과 ID 각각 1회 산출(타이밍 외부).
            seq_ids = await _retrieve_sequential(pq)
            par_ids = await _retrieve_parallel(pq)
            quality_rows.append(
                _QualityRow(query=row.query, seq_ids=seq_ids, par_ids=par_ids)
            )
            correct_by_query[row.query] = set(row.correct_ids)
        print()
    finally:
        # 앱 글로벌 엔진 dispose (lifespan 종료와 동일).
        await _database._on_ai_engine.dispose()
        await _database._on_data_engine.dispose()

    result = _build_result(
        args=args,
        sema_cap=sema_cap,
        weights=weights,
        holdout_path=holdout_path,
        timings=timings,
        quality_rows=quality_rows,
        correct_by_query=correct_by_query,
    )

    _print_report(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"결과 저장: {output_path}\n")


def _build_result(
    *,
    args: argparse.Namespace,
    sema_cap: int,
    weights: dict[str, float] | None,
    holdout_path: Path,
    timings: list[_QueryTiming],
    quality_rows: list[_QualityRow],
    correct_by_query: dict[str, set[str]],
) -> dict:
    """콘솔/JSON 리포트용 결과 dict 구성."""
    seq_medians = [t.seq_median_ms for t in timings]
    par_medians = [t.par_median_ms for t in timings]

    seq_overall = statistics.median(seq_medians) if seq_medians else 0.0
    par_overall = statistics.median(par_medians) if par_medians else 0.0
    speedup = (seq_overall / par_overall) if par_overall else 0.0

    # 품질: recall@k·MRR을 순차/병렬 각각 집계.
    seq_recall, seq_mrr = _quality_aggregate(
        quality_rows, correct_by_query, use_seq=True
    )
    par_recall, par_mrr = _quality_aggregate(
        quality_rows, correct_by_query, use_seq=False
    )

    # top-k 집합 일치 여부.
    match = 0
    mismatch_examples: list[dict] = []
    for qr in quality_rows:
        if set(qr.seq_ids) == set(qr.par_ids):
            match += 1
        else:
            mismatch_examples.append(
                {"query": qr.query, "seq_ids": qr.seq_ids, "par_ids": qr.par_ids}
            )
    mismatch = len(mismatch_examples)

    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "holdout_path": str(holdout_path),
        "total_queries": len(timings),
        "reps": args.reps,
        "sema_cap": sema_cap,
        "engine": "core.database (app global pool, ai+data session ctx)",
        "weights": weights,
        "latency": {
            "unit": "ms",
            "seq_median_ms": round(seq_overall, 3),
            "par_median_ms": round(par_overall, 3),
            "speedup": round(speedup, 3),
            "seq_p50_ms": round(_pct(seq_medians, 50), 3),
            "seq_p95_ms": round(_pct(seq_medians, 95), 3),
            "par_p50_ms": round(_pct(par_medians, 50), 3),
            "par_p95_ms": round(_pct(par_medians, 95), 3),
        },
        "quality": {
            "sequential": {
                **{f"recall@{k}": round(seq_recall[k], 4) for k in _AT_K},
                "mrr": round(seq_mrr, 4),
            },
            "parallel": {
                **{f"recall@{k}": round(par_recall[k], 4) for k in _AT_K},
                "mrr": round(par_mrr, 4),
            },
            "recall_delta": {
                f"recall@{k}": round(par_recall[k] - seq_recall[k], 4) for k in _AT_K
            },
            "mrr_delta": round(par_mrr - seq_mrr, 4),
        },
        "set_equivalence": {
            "match": match,
            "mismatch": mismatch,
            "mismatch_examples": mismatch_examples[:20],
        },
        "per_query_latency": [
            {
                "query": t.query,
                "seq_median_ms": round(t.seq_median_ms, 3),
                "par_median_ms": round(t.par_median_ms, 3),
                "seq_samples_ms": t.seq_samples_ms,
                "par_samples_ms": t.par_samples_ms,
            }
            for t in timings
        ],
        "settings_snapshot": {
            "rrf_k_constant": settings.rrf_k_constant,
            "rrf_top_k_final": settings.rrf_top_k_final,
            "vector_track_top_k": settings.vector_track_top_k,
            "rrf_unweighted_baseline": settings.rrf_unweighted_baseline,
            "vector_default_sub_intent": settings.vector_default_sub_intent,
            "vector_global_concurrency": settings.vector_global_concurrency,
        },
    }


def _quality_aggregate(
    rows: list[_QualityRow],
    correct_by_query: dict[str, set[str]],
    *,
    use_seq: bool,
) -> tuple[dict[int, float], float]:
    """순차/병렬 결과에 대한 평균 recall@k·MRR 집계(run_recall._compute_metrics 재사용)."""
    n = len(rows)
    if n == 0:
        return {k: 0.0 for k in _AT_K}, 0.0
    recall_sum = {k: 0.0 for k in _AT_K}
    rr_sum = 0.0
    for qr in rows:
        ids = qr.seq_ids if use_seq else qr.par_ids
        recall_at, rr = _compute_metrics(ids, correct_by_query.get(qr.query, set()))
        for k in _AT_K:
            recall_sum[k] += recall_at.get(k, 0.0)
        rr_sum += rr
    return {k: recall_sum[k] / n for k in _AT_K}, rr_sum / n


def _pct(values: list[float], pct: float) -> float:
    """리스트의 백분위값(선형 보간 없이 nearest-rank 근사)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[rank]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="병렬 검색 전(순차)/후(병렬) retrieval 구간 비교",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--holdout",
        default=str(_DEFAULT_HOLDOUT),
        metavar="PATH",
        help=f"eval_set_holdout.tsv 경로 (기본: {_DEFAULT_HOLDOUT})",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        metavar="PATH",
        help="결과 JSON 저장 경로 (기본: eval_results/compare_parallel_{ts}.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="처음 N건만 측정 (smoke test용)",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=5,
        metavar="N",
        help="질의별 측정 반복 횟수 (기본 5, 워밍업 1회 제외)",
    )
    parser.add_argument(
        "--sema-cap",
        type=int,
        default=None,
        metavar="N",
        help=(
            "병렬 경로 글로벌 세마포어 cap (기본: settings.vector_global_concurrency=40). "
            "lifespan 밖이라 미설정 시 병렬이 무제한이 되므로 반드시 init한다."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
