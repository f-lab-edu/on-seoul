# ruff: noqa: E402
"""가중치 그리드 서치 — 추천 조합 출력.

eval_set_holdout.tsv 의 VECTOR_SEARCH 질의를 대상으로 가중치 조합을 체계적으로
시험하여 recall@k / MRR 이 가장 높은 조합을 추천한다.

⚠ 주의
-------
  - 결과를 core/config.py 에 직접 반영하지 않는다 — 사람이 검수 후 수동 반영.
  - 봉인 평가셋(eval_set_holdout.tsv) 을 few-shot / 프롬프트에 노출하지 않는다.
  - SQL_SEARCH 질의는 무시한다 (가중치가 벡터 채널에만 적용되므로).

사용법
------
  # semantic 질의 기준 recall@10 최대화
  uv run python scripts/eval/tune_weights.py \\
      --grid "track_a:0.1,0.3,0.5;track_b:0.2,0.4;track_c:0.3,0.5;bm25:0.3,0.5" \\
      --metric recall@10 \\
      --sub-intent semantic

  # 전체 VECTOR_SEARCH 기준 MRR 최대화 (sub_intent 무관)
  uv run python scripts/eval/tune_weights.py \\
      --grid "track_a:0.1,0.5;track_b:0.2,0.5;track_c:0.2,0.5;bm25:0.2,0.5" \\
      --metric mrr

  # smoke test: holdout 5건만
  uv run python scripts/eval/tune_weights.py \\
      --grid "track_a:0.5;track_b:0.25;track_c:0.25;bm25:0.5" \\
      --metric recall@10 --limit 5

  # 결과 CSV 저장
  uv run python scripts/eval/tune_weights.py \\
      --grid "track_a:0.1,0.3,0.5;track_b:0.2,0.4;track_c:0.3,0.5;bm25:0.3,0.5" \\
      --output scripts/eval/eval_results/grid_search.csv
"""

import argparse
import asyncio
import csv
import itertools
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from llm.client import get_chat_model, get_embeddings
from scripts.eval.run_recall import EvalRow, load_holdout, run_eval

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_HOLDOUT = Path(__file__).resolve().parent / "eval_set_holdout.tsv"
_AT_K = (1, 5, 10)


# ---------------------------------------------------------------------------
# 그리드 파싱
# ---------------------------------------------------------------------------


def parse_grid(spec: str) -> list[dict[str, float]]:
    """
    그리드 스펙 파싱: "track_a:0.1,0.3;track_b:0.2,0.4" →
    [{"track_a":0.1,"track_b":0.2}, {"track_a":0.1,"track_b":0.4}, ...]

    형식: channel:v1,v2,...;channel:v1,v2,...
    """
    channels: dict[str, list[float]] = {}
    for part in spec.strip().split(";"):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(
                f"그리드 스펙 파싱 오류: '{part}' (형식: channel:v1,v2,...)"
            )
        ch, vals_str = part.split(":", 1)
        channels[ch.strip()] = [
            float(v.strip()) for v in vals_str.split(",") if v.strip()
        ]

    if not channels:
        raise ValueError("그리드 스펙이 비어 있습니다.")

    keys = list(channels.keys())
    value_lists = [channels[k] for k in keys]
    combos = []
    for combo in itertools.product(*value_lists):
        combos.append(dict(zip(keys, combo)))
    return combos


def _metric_value(metric_name: str, recall_at: dict, mrr: float) -> float:
    """지표 이름으로 값 추출."""
    if metric_name.lower() == "mrr":
        return mrr
    if metric_name.lower().startswith("recall@"):
        k = int(metric_name.split("@")[1])
        return recall_at.get(k, 0.0)
    raise ValueError(
        f"지원하지 않는 지표: {metric_name} (mrr | recall@1 | recall@5 | recall@10)"
    )


# ---------------------------------------------------------------------------
# 그리드 서치 실행
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    holdout_path = Path(args.holdout)
    if not holdout_path.exists():
        print(f"오류: holdout 파일 없음 — {holdout_path}", file=sys.stderr)
        print("평가셋을 먼저 구축하세요:")
        print("  uv run python scripts/eval/finalize_eval_set.py \\")
        print("      --input candidates_review.tsv --output eval_set_holdout.tsv")
        sys.exit(1)

    all_rows = load_holdout(holdout_path)

    # VECTOR_SEARCH 만 필터링 (가중치는 벡터 채널에만 적용)
    vec_rows: list[EvalRow] = [r for r in all_rows if r.intent == "VECTOR_SEARCH"]

    # sub_intent 필터
    if args.sub_intent:
        vec_rows = [r for r in vec_rows if r.sub_intent == args.sub_intent]

    if not vec_rows:
        print("오류: 해당 조건에 맞는 VECTOR_SEARCH 질의가 없습니다.", file=sys.stderr)
        sys.exit(1)

    if args.limit:
        vec_rows = vec_rows[: args.limit]

    print(
        f"평가 대상: {len(vec_rows)}건 (VECTOR_SEARCH"
        + (f" / {args.sub_intent}" if args.sub_intent else "")
        + ")"
    )

    # 그리드 파싱
    try:
        combos = parse_grid(args.grid)
    except ValueError as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"조합 수: {len(combos)}")
    print(f"측정 지표: {args.metric}\n")

    # DB 연결
    on_data_engine = create_async_engine(settings.on_data_database_url, echo=False)
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )

    from agents.sql_agent import SqlAgent

    try:
        OnData = async_sessionmaker(on_data_engine, expire_on_commit=False)
        OnAi = async_sessionmaker(on_ai_engine, expire_on_commit=False)
        embedder = get_embeddings()
        sql_agent = SqlAgent(model=get_chat_model())

        results_summary: list[dict] = []

        async with OnData() as data_session, OnAi() as ai_session:
            for i, weights in enumerate(combos, 1):
                weights_str = " ".join(
                    f"{k}={v:.2f}" for k, v in sorted(weights.items())
                )
                print(f"  [{i:>3}/{len(combos)}] {weights_str}", end="\r")

                metrics = await run_eval(
                    vec_rows,
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    sql_agent=sql_agent,
                    weights=weights,
                    vector_only=True,  # VECTOR_SEARCH만
                )

                n = len(metrics)
                recall_at = (
                    {
                        k: sum(m.recall_at.get(k, 0.0) for m in metrics) / n
                        for k in _AT_K
                    }
                    if n
                    else {k: 0.0 for k in _AT_K}
                )
                mrr = sum(m.rr for m in metrics) / n if n else 0.0
                score = _metric_value(args.metric, recall_at, mrr)

                results_summary.append(
                    {
                        "weights": weights,
                        "metric_value": round(score, 4),
                        **{f"recall@{k}": round(recall_at.get(k, 0), 4) for k in _AT_K},
                        "mrr": round(mrr, 4),
                    }
                )

    finally:
        await on_data_engine.dispose()
        await on_ai_engine.dispose()

    # 정렬 (내림차순)
    results_summary.sort(key=lambda x: x["metric_value"], reverse=True)

    # 출력
    print(f"\n{'=' * 70}")
    print(f"그리드 서치 결과 — {args.metric} 내림차순 (Top 10)")
    print(f"{'=' * 70}")
    header = f"  {'rank':>4}  {'metric':>8}  {'r@1':>6}  {'r@5':>6}  {'r@10':>6}  {'mrr':>8}  weights"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for rank, row in enumerate(results_summary[:10], 1):
        w_str = json.dumps(row["weights"], ensure_ascii=False)
        print(
            f"  {rank:>4}  {row['metric_value']:>8.4f}"
            f"  {row.get('recall@1', 0):>6.4f}"
            f"  {row.get('recall@5', 0):>6.4f}"
            f"  {row.get('recall@10', 0):>6.4f}"
            f"  {row['mrr']:>8.4f}  {w_str}"
        )

    if not results_summary:
        print("ERROR: 모든 그리드 조합이 실패했습니다.", file=sys.stderr)
        sys.exit(1)
    best = results_summary[0]
    print("\n[추천 가중치]")
    print(f"  {json.dumps(best['weights'], ensure_ascii=False, indent=4)}")
    print(
        f"\n  core/config.py 의 rrf_weight_profiles['{args.sub_intent or 'all'}'] 에 수동 반영 후"
    )
    print("  rrf_unweighted_baseline = False 로 전환하세요.\n")

    # CSV 저장 (선택)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = (
            ["weights_json", f"metric_{args.metric}"]
            + [f"recall@{k}" for k in _AT_K]
            + ["mrr"]
        )
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            for row in results_summary:
                writer.writerow(
                    {
                        "weights_json": json.dumps(row["weights"], ensure_ascii=False),
                        f"metric_{args.metric}": row["metric_value"],
                        **{f"recall@{k}": row.get(f"recall@{k}", 0) for k in _AT_K},
                        "mrr": row["mrr"],
                    }
                )
        print(f"CSV 저장: {output_path}")

    # 측정 메타 JSON
    meta = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "holdout_path": str(holdout_path),
        "sub_intent_filter": args.sub_intent,
        "metric": args.metric,
        "n_queries": len(vec_rows),
        "n_combinations": len(combos),
        "best": best,
        "all": results_summary,
    }
    meta_path = (
        Path(args.output).parent
        / f"grid_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
        if args.output
        else Path(__file__).resolve().parent
        / "eval_results"
        / f"grid_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    )
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"측정 메타 JSON: {meta_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RRF 가중치 그리드 서치 — 추천 조합 출력",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--grid",
        required=True,
        metavar="SPEC",
        help=(
            '가중치 그리드 스펙. 예: "track_a:0.1,0.3,0.5;track_b:0.2,0.4;track_c:0.3,0.5;bm25:0.3,0.5"'
        ),
    )
    parser.add_argument(
        "--metric",
        default="recall@10",
        choices=["recall@1", "recall@5", "recall@10", "mrr"],
        help="최적화 대상 지표 (기본: recall@10)",
    )
    parser.add_argument(
        "--sub-intent",
        default=None,
        choices=["identification", "detail", "semantic"],
        metavar="INTENT",
        help="특정 sub_intent 만 필터 (없으면 전체 VECTOR_SEARCH)",
    )
    parser.add_argument(
        "--holdout",
        default=str(_DEFAULT_HOLDOUT),
        metavar="PATH",
        help=f"eval_set_holdout.tsv 경로 (기본: {_DEFAULT_HOLDOUT})",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="결과 TSV 저장 경로 (선택)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="처음 N건만 측정 (smoke test용)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
