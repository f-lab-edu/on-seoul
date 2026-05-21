"""평가셋 후보 생성기.

실제 DB에 연결하여 질의별로 모든 검색 채널을 실행하고,
정답 레이블링을 위한 후보 목록을 출력한다.

두 가지 모드:

  --interactive
      REPL 방식. 질의를 직접 입력하고 결과를 보면서 정답 번호를 입력한다.
      eval_set_holdout.tsv 형식으로 바로 저장된다.

  --batch QUERIES_FILE
      queries_draft.tsv 를 읽어 모든 질의를 실행하고 candidates_review.tsv 를 출력한다.
      is_correct 컬럼이 비어 있으며, 사람이 검토 후 채우면 된다.
      채운 파일을 finalize_eval_set.py 에 넘기면 eval_set_holdout.tsv 가 생성된다.

사용법
------
  # 인터랙티브 모드
  uv run python scripts/eval/generate_candidates.py --interactive

  # 배치 모드
  uv run python scripts/eval/generate_candidates.py --batch scripts/eval/queries_draft.tsv

  # 배치 모드, 출력 파일 지정
  uv run python scripts/eval/generate_candidates.py \\
      --batch scripts/eval/queries_draft.tsv \\
      --output scripts/eval/candidates_review.tsv

  # 인텐트 분류 없이 벡터 검색만 (라우터 LLM 호출 생략)
  uv run python scripts/eval/generate_candidates.py --interactive --skip-router
"""

import argparse
import asyncio
import csv
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.router_agent import RouterAgent, _IntentOutput
from core.config import settings
from core.rrf import reciprocal_rank_fusion
from llm.client import get_embeddings
from schemas.state import IntentType
from tools.bm25_search import bm25_search
from tools.hydrate_services import hydrate_services
from tools.question_search import question_search
from tools.tokenizer import tokenize_query
from tools.vector_search import vector_search

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# 각 채널에서 가져올 후보 수 — 평가용이므로 운영보다 넉넉하게
_EVAL_TOP_K = 20
_EVAL_SCAN_K = 80


@dataclass
class Candidate:
    service_id: str
    service_name: str
    area_name: str
    max_class_name: str
    service_status: str
    service_url: str
    channels: list[str]  # 이 결과를 반환한 채널 목록
    score: float         # rrf_score 또는 유사도


@dataclass
class QueryResult:
    query: str
    intent: str
    sub_intent: str
    refined_query: str
    candidates: list[Candidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 검색 실행
# ---------------------------------------------------------------------------

async def _run_vector(
    query: str,
    intent_output: _IntentOutput,
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
) -> list[Candidate]:
    """4채널 검색 + RRF + Hydration."""
    refined = intent_output.refined_query or query
    vec = await embedder.aembed_query(refined)

    filters = dict(
        max_class_name=intent_output.max_class_name,
        area_name=intent_output.area_name,
        service_status=intent_output.service_status,
    )

    # Track A — identity, post-filter 적용
    try:
        a_rows = await vector_search(
            ai_session, vec, row_kind="identity",
            top_k=_EVAL_TOP_K, min_similarity=0.5, **filters
        )
    except Exception as e:
        logger.warning("track_a 실패: %s", e)
        a_rows = []

    # Track B — summary, post-filter 미적용
    try:
        b_rows = await vector_search(
            ai_session, vec, row_kind="summary",
            top_k=_EVAL_TOP_K, min_similarity=0.5,
        )
    except Exception as e:
        logger.warning("track_b 실패: %s", e)
        b_rows = []

    # Track C — question, service_id별 dedup
    try:
        c_rows = await question_search(
            ai_session, vec, top_k=_EVAL_TOP_K, min_similarity=0.5,
        )
    except Exception as e:
        logger.warning("track_c 실패: %s", e)
        c_rows = []

    # BM25
    tokens = tokenize_query(refined)
    try:
        d_rows = await bm25_search(ai_session, tokens, top_k=_EVAL_TOP_K) if tokens else []
    except Exception as e:
        logger.warning("bm25 실패: %s", e)
        d_rows = []

    # 채널별 service_id 집합 (어느 채널에서 왔는지 추적)
    channel_hits: dict[str, set[str]] = {
        "track_a": {r["service_id"] for r in a_rows},
        "track_b": {r["service_id"] for r in b_rows},
        "track_c": {r["service_id"] for r in c_rows},
        "bm25":    {r["service_id"] for r in d_rows},
    }

    merged = reciprocal_rank_fusion(
        {ch: [r["service_id"] for r in rows]
         for ch, rows in [("track_a", a_rows), ("track_b", b_rows),
                          ("track_c", c_rows), ("bm25", d_rows)]},
        weights=None,  # 평가용 baseline: 비가중치
        k_constant=60,
    )

    service_ids = [sid for sid, _ in merged[:_EVAL_TOP_K]]
    rrf_scores  = {sid: score for sid, score in merged}

    hydrated = await hydrate_services(data_session, service_ids)

    candidates = []
    for row in hydrated:
        sid = row["service_id"]
        contributed = [ch for ch, ids in channel_hits.items() if sid in ids]
        candidates.append(Candidate(
            service_id=sid,
            service_name=row.get("service_name", ""),
            area_name=row.get("area_name", ""),
            max_class_name=row.get("max_class_name", ""),
            service_status=row.get("service_status", ""),
            service_url=row.get("service_url", ""),
            channels=contributed,
            score=rrf_scores.get(sid, 0.0),
        ))
    return candidates


async def _run_sql(
    intent_output: _IntentOutput,
    *,
    data_session: AsyncSession,
) -> list[Candidate]:
    """SQL 검색."""
    from tools.sql_search import sql_search
    rows = await sql_search(
        data_session,
        max_class_name=intent_output.max_class_name,
        area_name=intent_output.area_name,
        service_status=intent_output.service_status,
        keyword=intent_output.refined_query,
        top_k=_EVAL_TOP_K,
    )
    return [
        Candidate(
            service_id=r["service_id"],
            service_name=r.get("service_name", ""),
            area_name=r.get("area_name", ""),
            max_class_name=r.get("max_class_name", ""),
            service_status=r.get("service_status", ""),
            service_url=r.get("service_url", ""),
            channels=["sql"],
            score=float(len(rows) - i),  # rank 역산
        )
        for i, r in enumerate(rows)
    ]


async def run_query(
    query: str,
    intent_output: _IntentOutput,
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
) -> QueryResult:
    intent = intent_output.intent

    if intent == IntentType.VECTOR_SEARCH:
        candidates = await _run_vector(
            query, intent_output,
            ai_session=ai_session, data_session=data_session, embedder=embedder,
        )
    elif intent == IntentType.SQL_SEARCH:
        candidates = await _run_sql(intent_output, data_session=data_session)
    else:
        candidates = []

    return QueryResult(
        query=query,
        intent=intent.value,
        sub_intent=intent_output.vector_sub_intent or "",
        refined_query=intent_output.refined_query or query,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# 출력 포맷
# ---------------------------------------------------------------------------

def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def print_result(result: QueryResult) -> None:
    print()
    print(f"  Intent  : {result.intent}" + (f" / {result.sub_intent}" if result.sub_intent else ""))
    print(f"  Refined : {result.refined_query}")
    print()

    if not result.candidates:
        print("  결과 없음")
        return

    header = f"  {'#':>2}  {'service_id':<14}  {'service_name':<30}  {'지역':<8}  {'카테고리':<10}  {'상태':<10}  채널"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for i, c in enumerate(result.candidates, 1):
        ch = ",".join(c.channels)
        print(
            f"  {i:>2}  {c.service_id:<14}  "
            f"{_truncate(c.service_name, 30):<30}  "
            f"{_truncate(c.area_name, 8):<8}  "
            f"{_truncate(c.max_class_name, 10):<10}  "
            f"{_truncate(c.service_status, 10):<10}  {ch}"
        )
    print()


# ---------------------------------------------------------------------------
# 인터랙티브 모드
# ---------------------------------------------------------------------------

async def interactive_loop(
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
    router: RouterAgent | None,
    output_path: Path,
) -> None:
    """질의를 직접 입력하고 정답 번호를 지정한다."""
    records: list[dict] = []

    print("\n평가셋 생성기 — interactive 모드")
    print("종료: q / 스킵: s (정답 없음으로 기록) / 정답 없음: Enter\n")

    while True:
        try:
            query = input("질의 입력> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if query.lower() == "q":
            break
        if not query:
            continue

        # 인텐트 분류
        if router is not None:
            print("  분류 중...", end="\r")
            intent_output = await router.classify(query)
        else:
            # 라우터 없이 직접 지정
            intent_str = input("  intent (V=vector, S=sql, skip=fallback) [V]: ").strip().upper() or "V"
            intent_map = {"V": IntentType.VECTOR_SEARCH, "S": IntentType.SQL_SEARCH}
            from agents.router_agent import _IntentOutput
            intent_output = _IntentOutput(intent=intent_map.get(intent_str, IntentType.VECTOR_SEARCH))

        print(f"  검색 중... (intent={intent_output.intent.value})", end="\r")
        result = await run_query(
            query, intent_output,
            ai_session=ai_session, data_session=data_session, embedder=embedder,
        )
        print(f"  질의: {query}")
        print_result(result)

        if not result.candidates:
            records.append({
                "query": query, "intent": result.intent,
                "sub_intent": result.sub_intent, "correct_service_ids": "",
            })
            continue

        answer_input = input("  정답 번호 (쉼표 구분, 없으면 Enter, 스킵 s): ").strip()
        if answer_input.lower() == "s":
            print("  스킵됨\n")
            continue

        correct_ids: list[str] = []
        if answer_input:
            for token in answer_input.split(","):
                token = token.strip()
                if token.isdigit():
                    idx = int(token) - 1
                    if 0 <= idx < len(result.candidates):
                        correct_ids.append(result.candidates[idx].service_id)

        records.append({
            "query": query,
            "intent": result.intent,
            "sub_intent": result.sub_intent,
            "correct_service_ids": ",".join(correct_ids),
        })
        print(f"  저장됨 (정답 {len(correct_ids)}건)\n")

    if records:
        _write_holdout(records, output_path)
        print(f"\n{len(records)}건 → {output_path}")
    else:
        print("\n저장된 항목 없음")


def _write_holdout(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["query", "intent", "sub_intent", "correct_service_ids"]
    mode = "a" if path.exists() else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        if mode == "w":
            writer.writeheader()
        writer.writerows(records)


# ---------------------------------------------------------------------------
# 배치 모드
# ---------------------------------------------------------------------------

async def batch_run(
    queries_file: Path,
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder,
    router: RouterAgent | None,
    output_path: Path,
) -> None:
    """queries_draft.tsv → candidates_review.tsv."""
    queries = _load_queries(queries_file)
    print(f"{len(queries)}개 질의 처리 중...")

    rows: list[dict] = []

    for i, (query, intent_hint, sub_intent_hint, _) in enumerate(queries, 1):
        print(f"  [{i:>3}/{len(queries)}] {query[:40]}")

        # 인텐트: 파일에 힌트가 있으면 우선 사용, 없으면 라우터 분류
        if intent_hint and intent_hint in (t.value for t in IntentType):
            from agents.router_agent import _IntentOutput
            intent_output = _IntentOutput(
                intent=IntentType(intent_hint),
                vector_sub_intent=sub_intent_hint or None,
            )
        elif router is not None:
            intent_output = await router.classify(query)
        else:
            from agents.router_agent import _IntentOutput
            intent_output = _IntentOutput(intent=IntentType.VECTOR_SEARCH)

        try:
            result = await run_query(
                query, intent_output,
                ai_session=ai_session, data_session=data_session, embedder=embedder,
            )
        except Exception as e:
            logger.error("질의 실패 [%s]: %s", query, e)
            result = QueryResult(
                query=query, intent=intent_output.intent.value,
                sub_intent=intent_output.vector_sub_intent or "",
                refined_query=intent_output.refined_query or query,
                candidates=[],
            )

        if not result.candidates:
            rows.append(_candidate_row(result, None, rank=0))
        else:
            for rank, c in enumerate(result.candidates, 1):
                rows.append(_candidate_row(result, c, rank=rank))

    _write_candidates(rows, output_path)
    print(f"\n완료: {len(rows)}행 → {output_path}")
    print("is_correct 컬럼(y/공백)을 채운 뒤 finalize_eval_set.py 를 실행하세요.")


def _candidate_row(result: QueryResult, c: Candidate | None, rank: int) -> dict:
    return {
        "query":          result.query,
        "intent":         result.intent,
        "sub_intent":     result.sub_intent,
        "refined_query":  result.refined_query,
        "rank":           rank,
        "service_id":     c.service_id if c else "",
        "service_name":   c.service_name if c else "",
        "area_name":      c.area_name if c else "",
        "max_class_name": c.max_class_name if c else "",
        "service_status": c.service_status if c else "",
        "channels":       ",".join(c.channels) if c else "",
        "score":          f"{c.score:.4f}" if c else "",
        "is_correct":     "",  # 사람이 채움
    }


def _write_candidates(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "query", "intent", "sub_intent", "refined_query",
        "rank", "service_id", "service_name", "area_name", "max_class_name",
        "service_status", "channels", "score", "is_correct",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _load_queries(path: Path) -> list[tuple[str, str, str, str]]:
    """TSV에서 (query, intent, sub_intent, notes) 리스트 반환."""
    results = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            results.append((
                row.get("query", "").strip(),
                row.get("intent", "").strip(),
                row.get("sub_intent", "").strip(),
                row.get("notes", "").strip(),
            ))
    return [(q, i, s, n) for q, i, s, n in results if q]


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    on_data_engine = create_async_engine(settings.on_data_database_url, echo=False)
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url, echo=False,
        connect_args={"statement_cache_size": 0},
    )

    try:
        OnData = async_sessionmaker(on_data_engine, expire_on_commit=False)
        OnAi = async_sessionmaker(on_ai_engine, expire_on_commit=False)
        embedder = get_embeddings()

        router: RouterAgent | None = None
        if not args.skip_router:
            from llm.client import get_chat_model
            router = RouterAgent(model=get_chat_model())

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")

        async with OnData() as data_session, OnAi() as ai_session:
            if args.mode == "interactive":
                output = args.output or Path(f"scripts/eval/holdout_draft_{timestamp}.tsv")
                await interactive_loop(
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    router=router,
                    output_path=Path(output),
                )
            else:
                queries_file = Path(args.batch)
                output = args.output or Path(
                    f"scripts/eval/candidates_review_{timestamp}.tsv"
                )
                await batch_run(
                    queries_file,
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    router=router,
                    output_path=Path(output),
                )
    finally:
        await on_data_engine.dispose()
        await on_ai_engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="평가셋 후보 생성기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--interactive", dest="mode", action="store_const", const="interactive",
                      help="REPL 방식: 직접 질의 입력 → 정답 선택")
    mode.add_argument("--batch", metavar="QUERIES_FILE",
                      help="배치 방식: queries_draft.tsv 읽어 candidates_review.tsv 출력")

    parser.add_argument("--output", metavar="OUTPUT_FILE",
                        help="출력 파일 경로 (기본값: 타임스탬프 자동 생성)")
    parser.add_argument("--skip-router", action="store_true",
                        help="RouterAgent LLM 호출 생략 (벡터 검색 only)")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
