# ruff: noqa: E402
"""봉인 평가셋으로 recall@k / MRR 측정.

eval_set_holdout.tsv 를 읽어 각 질의에 대해 실제 검색을 수행하고
정답 service_id 가 결과 몇 번째에 등장하는지 측정한다.

의도 유형별 분리 측정:
  - SQL_SEARCH
  - VECTOR_SEARCH / identification
  - VECTOR_SEARCH / detail
  - VECTOR_SEARCH / semantic

측정 지표: recall@1, recall@5, recall@10, MRR

사용법
------
  # 전체 평가셋 측정 + 결과 저장
  uv run python scripts/eval/run_recall.py

  # 출력 파일 지정
  uv run python scripts/eval/run_recall.py \\
      --holdout scripts/eval/eval_set_holdout.tsv \\
      --output scripts/eval/eval_results/baseline.json

  # 일부만 (smoke test)
  uv run python scripts/eval/run_recall.py --limit 10

  # 벡터 검색만 (SQL 스킵)
  uv run python scripts/eval/run_recall.py --vector-only

  # 가중치 오버라이드 (tune_weights.py 내부에서 호출할 때 사용)
  uv run python scripts/eval/run_recall.py \\
      --weights '{"track_a":0.5,"track_b":0.25,"track_c":0.25,"bm25":0.5}'

주의
----
  - eval_set_holdout.tsv 는 봉인 평가셋이다. 프롬프트·few-shot 에 사용 금지.
  - 실제 DB 연결이 필요하다. .env 에 ON_AI_DATABASE_URL / ON_DATA_DATABASE_URL 필수.
"""

import argparse
import asyncio
import csv
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.sql_agent import SqlAgent
from core.config import settings
from core.rrf import reciprocal_rank_fusion
from llm.client import get_chat_model, get_embeddings
from schemas.state import IntentType
from tools.bm25_search import bm25_search
from tools.hydrate_services import hydrate_services
from tools.question_search import question_search
from tools.tokenizer import tokenize_query
from tools.vector_search import vector_search

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_HOLDOUT = Path(__file__).resolve().parent / "eval_set_holdout.tsv"
_DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent
    / "eval_results"
    / f"{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
)

_AT_K = (1, 5, 10)
_SQL_TOP_K = 20
_VEC_TOP_K = 20
_VEC_SCAN_K = 80


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------


@dataclass
class EvalRow:
    query: str
    intent: str
    sub_intent: str
    correct_ids: list[str]  # 정답 service_id 목록 (순서 무관)


@dataclass
class QueryMetric:
    query: str
    intent: str
    sub_intent: str
    correct_ids: list[str]
    result_ids: list[str]  # 검색 결과 service_id 목록 (순위 순)
    recall_at: dict[int, float] = field(default_factory=dict)  # {k: 0 or 1}
    rr: float = 0.0  # Reciprocal Rank (0 if not found)


@dataclass
class GroupMetrics:
    group: str  # e.g. "VECTOR_SEARCH/semantic"
    count: int
    recall_at: dict[int, float] = field(default_factory=dict)  # mean recall@k
    mrr: float = 0.0


@dataclass
class EvalResult:
    timestamp: str
    holdout_path: str
    weights: dict[str, float] | None
    total_queries: int
    overall: dict  # recall@k + mrr
    by_group: list[dict]  # GroupMetrics per intent/sub_intent
    per_query: list[dict]  # QueryMetric 상세
    settings_snapshot: dict


# ---------------------------------------------------------------------------
# 평가셋 로드
# ---------------------------------------------------------------------------


def load_holdout(path: Path) -> list[EvalRow]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            cids = [
                s.strip()
                for s in row.get("correct_service_ids", "").split(",")
                if s.strip()
            ]
            rows.append(
                EvalRow(
                    query=row["query"].strip(),
                    intent=row.get("intent", "VECTOR_SEARCH").strip(),
                    sub_intent=row.get("sub_intent", "").strip(),
                    correct_ids=cids,
                )
            )
    return [r for r in rows if r.query and r.correct_ids]


# ---------------------------------------------------------------------------
# 검색 실행
# ---------------------------------------------------------------------------


async def _search_vector(
    row: EvalRow,
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
    weights: dict[str, float] | None,
) -> list[str]:
    """4채널 + RRF → service_id 순위 리스트."""
    vec = await embedder.aembed_query(row.query)

    # Track A — identity
    try:
        a_rows = await vector_search(
            ai_session,
            vec,
            row_kind="identity",
            top_k=_VEC_TOP_K,
            min_similarity=0.5,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_a 실패 [%s]: %s", row.query[:30], e)
        a_rows = []

    # Track B — summary
    try:
        b_rows = await vector_search(
            ai_session,
            vec,
            row_kind="summary",
            top_k=_VEC_TOP_K,
            min_similarity=0.5,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_b 실패 [%s]: %s", row.query[:30], e)
        b_rows = []

    # Track C — question (PARTITION BY dedup)
    try:
        c_rows = await question_search(
            ai_session,
            vec,
            top_k=_VEC_TOP_K,
            min_similarity=0.5,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_c 실패 [%s]: %s", row.query[:30], e)
        c_rows = []

    # BM25
    tokens = tokenize_query(row.query)
    try:
        d_rows = (
            await bm25_search(ai_session, tokens, limit=_VEC_TOP_K) if tokens else []
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("bm25 실패 [%s]: %s", row.query[:30], e)
        d_rows = []

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

    service_ids = [sid for sid, _ in merged[:_VEC_TOP_K]]
    hydrated = await hydrate_services(data_session, service_ids)
    return [r["service_id"] for r in hydrated]


async def _search_sql(
    row: EvalRow,
    *,
    data_session: AsyncSession,
    sql_agent: SqlAgent,
) -> list[str]:
    """SqlAgent → service_id 순위 리스트."""
    state: dict = {
        "message": row.query,
        "refined_query": None,
        "max_class_name": None,
        "area_name": None,
        "service_status": None,
    }
    result_state = await sql_agent.search(state, data_session, top_k=_SQL_TOP_K)  # type: ignore[arg-type]
    rows = result_state.get("sql_results") or []
    return [r["service_id"] for r in rows]


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------


def _compute_metrics(
    result_ids: list[str], correct_ids: set[str]
) -> tuple[dict[int, float], float]:
    """(recall_at, rr) 반환."""
    recall_at: dict[int, float] = {}
    rr = 0.0

    for k in _AT_K:
        top = set(result_ids[:k])
        recall_at[k] = 1.0 if top & correct_ids else 0.0

    for rank, sid in enumerate(result_ids, 1):
        if sid in correct_ids:
            rr = 1.0 / rank
            break

    return recall_at, rr


def _aggregate_group(metrics: list[QueryMetric], group: str) -> GroupMetrics:
    n = len(metrics)
    if n == 0:
        return GroupMetrics(
            group=group, count=0, recall_at={k: 0.0 for k in _AT_K}, mrr=0.0
        )

    recall_at = {k: sum(m.recall_at.get(k, 0.0) for m in metrics) / n for k in _AT_K}
    mrr = sum(m.rr for m in metrics) / n
    return GroupMetrics(group=group, count=n, recall_at=recall_at, mrr=mrr)


# ---------------------------------------------------------------------------
# 메인 평가 루프
# ---------------------------------------------------------------------------


async def run_eval(
    eval_rows: list[EvalRow],
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
    sql_agent: SqlAgent,
    weights: dict[str, float] | None,
    vector_only: bool = False,
) -> list[QueryMetric]:
    results: list[QueryMetric] = []

    for i, row in enumerate(eval_rows, 1):
        print(f"  [{i:>3}/{len(eval_rows)}] {row.query[:50]}", end="\r")
        try:
            if row.intent == IntentType.SQL_SEARCH.value and not vector_only:
                result_ids = await _search_sql(
                    row, data_session=data_session, sql_agent=sql_agent
                )
            else:
                result_ids = await _search_vector(
                    row,
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    weights=weights,
                )
        except Exception as e:
            logger.error("검색 실패 [%s]: %s", row.query[:40], e)
            result_ids = []

        recall_at, rr = _compute_metrics(result_ids, set(row.correct_ids))
        results.append(
            QueryMetric(
                query=row.query,
                intent=row.intent,
                sub_intent=row.sub_intent,
                correct_ids=row.correct_ids,
                result_ids=result_ids[:10],  # 상위 10개만 저장
                recall_at=recall_at,
                rr=rr,
            )
        )

    print()
    return results


# ---------------------------------------------------------------------------
# 리포트 출력
# ---------------------------------------------------------------------------


def _print_report(result: EvalResult) -> None:
    print(f"\n{'=' * 60}")
    print(f"평가 결과  |  총 {result['total_queries']}건")
    print(f"{'=' * 60}")

    overall = result["overall"]
    print("\n[전체]")
    for k in _AT_K:
        print(f"  recall@{k:>2}: {overall.get(f'recall@{k}', 0):.4f}")
    print(f"  MRR     : {overall.get('mrr', 0):.4f}")

    print("\n[그룹별]")
    for grp in result["by_group"]:
        print(f"\n  {grp['group']} (n={grp['count']})")
        for k in _AT_K:
            print(
                f"    recall@{k:>2}: {grp.get('recall_at', {}).get(str(k), grp.get('recall_at', {}).get(k, 0)):.4f}"
            )
        print(f"    MRR     : {grp.get('mrr', 0):.4f}")

    # 정답 미포함 질의 목록
    misses = [
        q
        for q in result["per_query"]
        if q["recall_at"].get(10, q["recall_at"].get("10", 0)) == 0
    ]
    if misses:
        print(f"\n[recall@10 miss — {len(misses)}건]")
        for m in misses[:10]:
            print(f"  - {m['query'][:60]}")
        if len(misses) > 10:
            print(f"  ... 외 {len(misses) - 10}건")

    print(f"\n{'=' * 60}\n")


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    holdout_path = Path(args.holdout)
    if not holdout_path.exists():
        print(f"오류: holdout 파일 없음 — {holdout_path}", file=sys.stderr)
        print("평가셋을 먼저 구축하세요:")
        print(
            "  uv run python scripts/eval/finalize_eval_set.py --input candidates_review.tsv --output eval_set_holdout.tsv"
        )
        sys.exit(1)

    eval_rows = load_holdout(holdout_path)
    if not eval_rows:
        print("오류: 정답이 있는 질의가 없습니다.", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        eval_rows = eval_rows[: args.limit]

    print(f"평가셋 로드: {len(eval_rows)}건 (from {holdout_path})")

    # 가중치 파싱
    weights: dict[str, float] | None = None
    if args.weights:
        try:
            weights = json.loads(args.weights)
        except json.JSONDecodeError as e:
            print(f"오류: --weights JSON 파싱 실패 — {e}", file=sys.stderr)
            sys.exit(1)
    elif not settings.rrf_unweighted_baseline:
        sub = settings.vector_default_sub_intent
        weights = settings.rrf_weight_profiles.get(sub)

    # DB 연결
    on_data_engine = create_async_engine(settings.on_data_database_url, echo=False)
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )

    try:
        OnData = async_sessionmaker(on_data_engine, expire_on_commit=False)
        OnAi = async_sessionmaker(on_ai_engine, expire_on_commit=False)
        embedder = get_embeddings()
        sql_agent = SqlAgent(model=get_chat_model())

        print(
            f"측정 시작 (weights={'None (unweighted)' if weights is None else weights})"
        )
        async with OnData() as data_session, OnAi() as ai_session:
            metrics = await run_eval(
                eval_rows,
                ai_session=ai_session,
                data_session=data_session,
                embedder=embedder,
                sql_agent=sql_agent,
                weights=weights,
                vector_only=args.vector_only,
            )
    finally:
        await on_data_engine.dispose()
        await on_ai_engine.dispose()

    # 그룹별 집계
    from collections import defaultdict

    by_group: dict[str, list[QueryMetric]] = defaultdict(list)
    for m in metrics:
        key = f"{m.intent}/{m.sub_intent}" if m.sub_intent else m.intent
        by_group[key].append(m)

    overall_agg = _aggregate_group(metrics, "overall")
    group_aggs = [_aggregate_group(v, k) for k, v in sorted(by_group.items())]

    def _grp_to_dict(g: GroupMetrics) -> dict:
        return {
            "group": g.group,
            "count": g.count,
            "recall_at": {k: round(v, 4) for k, v in g.recall_at.items()},
            "mrr": round(g.mrr, 4),
        }

    def _qm_to_dict(m: QueryMetric) -> dict:
        return {
            "query": m.query,
            "intent": m.intent,
            "sub_intent": m.sub_intent,
            "correct_ids": m.correct_ids,
            "result_ids": m.result_ids,
            "recall_at": {str(k): round(v, 4) for k, v in m.recall_at.items()},
            "rr": round(m.rr, 4),
        }

    result: dict = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "holdout_path": str(holdout_path),
        "weights": weights,
        "total_queries": len(metrics),
        "overall": {
            **{f"recall@{k}": round(overall_agg.recall_at.get(k, 0), 4) for k in _AT_K},
            "mrr": round(overall_agg.mrr, 4),
        },
        "by_group": [_grp_to_dict(g) for g in group_aggs],
        "per_query": [_qm_to_dict(m) for m in metrics],
        "settings_snapshot": {
            "rrf_k_constant": settings.rrf_k_constant,
            "rrf_unweighted_baseline": settings.rrf_unweighted_baseline,
            "vector_sub_intent_enabled": settings.vector_sub_intent_enabled,
            "vector_default_sub_intent": settings.vector_default_sub_intent,
        },
    }

    _print_report(result)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"결과 저장: {output_path}\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="봉인 평가셋으로 recall@k / MRR 측정",
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
        help="결과 JSON 저장 경로 (기본: eval_results/{timestamp}.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="처음 N건만 측정 (smoke test용)",
    )
    parser.add_argument(
        "--vector-only",
        action="store_true",
        help="SQL_SEARCH 질의도 벡터 검색으로 실행 (SQL 스킵)",
    )
    parser.add_argument(
        "--weights",
        default=None,
        metavar="JSON",
        help='가중치 JSON 오버라이드. 예: \'{"track_a":0.5,"track_b":0.25,"track_c":0.25,"bm25":0.5}\'',
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
