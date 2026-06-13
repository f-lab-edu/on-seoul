# ruff: noqa: E402
"""벡터 트랙별 유사도 점수 분포 측정 (min_similarity 하한 산정용).

운영 데이터(chat_search_results)는 min_similarity=0.6 컷을 통과한 행만 남아
왼쪽이 절단된 분포다. 이 스크립트는 봉인 평가셋 질의를 하한 없이(0.0) 깊게
(top_k 기본 50) 실행해 절단 없는 분포를 측정한다.

산출:
  - 트랙별(identity/summary/question) 순위 구간별 점수 분포
  - 정답 service_id 가 걸린 점수 통계 (하한을 올리면 정답을 얼마나 잃는지)
  - 후보 하한(0.5 / 0.55 / 0.6 / 0.65)별 정답 손실 건수

사용법
------
  uv run python scripts/eval/score_distribution.py
  uv run python scripts/eval/score_distribution.py --limit 5          # smoke
  uv run python scripts/eval/score_distribution.py --top-k 50
  uv run python scripts/eval/score_distribution.py --all-intents      # SQL_SEARCH 질의 포함

주의
----
  - eval_set_holdout.tsv 는 봉인 평가셋이다. 프롬프트/few-shot 에 사용 금지.
  - 실제 DB 연결이 필요하다 (.env 의 ON_AI_DATABASE_URL / ON_DATA_DATABASE_URL).
"""

import argparse
import asyncio
import csv
import json
import logging
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from llm.client import get_embeddings
from tools.question_search import question_search
from tools.vector_search import vector_search

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DEFAULT_HOLDOUT = Path(__file__).resolve().parent / "eval_set_holdout.tsv"
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "eval_results"

_TRACKS = ("identity", "summary", "question")
_RANK_MARKS = (1, 5, 10, 20, 30, 50)  # 이 순위의 점수를 기록
_FLOOR_CANDIDATES = (0.50, 0.55, 0.60, 0.65)
_HIST_BINS = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30 ~ 0.90


def load_holdout(path: Path, *, vector_only: bool) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            intent = row.get("intent", "VECTOR_SEARCH").strip()
            if vector_only and intent != "VECTOR_SEARCH":
                continue
            cids = [
                s.strip()
                for s in row.get("correct_service_ids", "").split(",")
                if s.strip()
            ]
            if row.get("query", "").strip() and cids:
                rows.append(
                    {
                        "query": row["query"].strip(),
                        "intent": intent,
                        "correct_ids": set(cids),
                    }
                )
    return rows


async def _track_rows(session, vec, *, track: str, top_k: int) -> list[dict]:
    if track == "question":
        return await question_search(session, vec, top_k=top_k, min_similarity=0.0)
    return await vector_search(
        session, vec, row_kind=track, top_k=top_k, min_similarity=0.0
    )


def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(int(len(s) * p), len(s) - 1)
    return s[idx]


async def main(args: argparse.Namespace) -> None:
    holdout = load_holdout(Path(args.holdout), vector_only=not args.all_intents)
    if args.limit:
        holdout = holdout[: args.limit]
    if not holdout:
        print("오류: 평가셋이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)
    print(f"평가셋 로드: {len(holdout)}건 / top_k={args.top_k} / min_similarity=0.0")

    engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    embedder = get_embeddings()

    # track → 수집 슬롯
    rank_scores: dict[str, dict[int, list[float]]] = {
        t: defaultdict(list) for t in _TRACKS
    }  # rank_mark → [score]
    all_scores: dict[str, list[float]] = {t: [] for t in _TRACKS}
    correct_scores: dict[str, list[float]] = {t: [] for t in _TRACKS}

    try:
        async with Session() as session:
            for i, row in enumerate(holdout, 1):
                print(f"  [{i:>3}/{len(holdout)}] {row['query'][:50]}", end="\r")
                vec = await embedder.aembed_query(row["query"])
                for track in _TRACKS:
                    try:
                        results = await _track_rows(
                            session, vec, track=track, top_k=args.top_k
                        )
                    except Exception as e:
                        await session.rollback()
                        logger.warning("%s 실패 [%s]: %s", track, row["query"][:30], e)
                        continue
                    for rank, r in enumerate(results, 1):
                        score = float(r["similarity"])
                        all_scores[track].append(score)
                        if rank in _RANK_MARKS:
                            rank_scores[track][rank].append(score)
                        if r["service_id"] in row["correct_ids"]:
                            correct_scores[track].append(score)
    finally:
        await engine.dispose()
    print()

    # ---- 리포트 ----
    report: dict = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "holdout": str(args.holdout),
        "queries": len(holdout),
        "top_k": args.top_k,
        "tracks": {},
    }

    for track in _TRACKS:
        scores = all_scores[track]
        correct = correct_scores[track]
        print(f"\n{'=' * 64}\n[{track}]  전체 hit {len(scores)}건, 정답 hit {len(correct)}건")

        print("  순위별 점수 (중앙값):")
        rank_summary = {}
        for mark in _RANK_MARKS:
            vals = rank_scores[track][mark]
            med = statistics.median(vals) if vals else None
            rank_summary[mark] = round(med, 4) if med is not None else None
            if med is not None:
                print(f"    rank {mark:>2}: {med:.3f}  (n={len(vals)})")

        hist = {}
        for lo in _HIST_BINS[:-1]:
            hi = round(lo + 0.05, 2)
            cnt = sum(1 for s in scores if lo <= s < hi)
            hist[f"{lo:.2f}~{hi:.2f}"] = cnt
        print("  점수 히스토그램:", {k: v for k, v in hist.items() if v})

        floor_loss = {}
        for floor in _FLOOR_CANDIDATES:
            lost = sum(1 for s in correct if s < floor)
            pct = (lost / len(correct) * 100) if correct else 0.0
            floor_loss[str(floor)] = {"lost": lost, "pct": round(pct, 1)}
            print(f"  하한 {floor:.2f} → 정답 손실 {lost}건 ({pct:.1f}%)")

        if correct:
            print(
                f"  정답 점수: min={min(correct):.3f}  p10={_pct(correct, 0.10):.3f}  "
                f"median={statistics.median(correct):.3f}  max={max(correct):.3f}"
            )

        report["tracks"][track] = {
            "total_hits": len(scores),
            "correct_hits": len(correct),
            "rank_median": rank_summary,
            "histogram": hist,
            "floor_loss": floor_loss,
            "correct_stats": (
                {
                    "min": round(min(correct), 4),
                    "p10": round(_pct(correct, 0.10), 4),
                    "median": round(statistics.median(correct), 4),
                    "max": round(max(correct), 4),
                }
                if correct
                else None
            ),
        }

    _DEFAULT_OUTPUT_DIR.mkdir(exist_ok=True)
    out = args.output or (
        _DEFAULT_OUTPUT_DIR
        / f"score_distribution_{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}.json"
    )
    Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n저장: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", default=str(_DEFAULT_HOLDOUT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--all-intents",
        action="store_true",
        help="SQL_SEARCH 질의도 포함 (기본: VECTOR_SEARCH만)",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
