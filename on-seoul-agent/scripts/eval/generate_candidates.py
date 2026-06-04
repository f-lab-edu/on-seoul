# ruff: noqa: E402
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

# on-seoul-agent 루트 (scripts/eval/../../..)
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agents.hydration_node import _filter_by_payment
from agents.router_agent import RouterAgent, _IntentOutput
from agents.sql_agent import SqlAgent
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
    score: float  # rrf_score 또는 유사도


@dataclass
class QueryResult:
    query: str
    intent: str
    sub_intent: str
    refined_query: str
    # 라우터가 추출한 필터 메타데이터 — 디버깅·후처리 분석용
    reasoning: str = ""
    extracted_max_class_name: str = ""
    extracted_area_name: str = ""
    extracted_service_status: str = ""
    extracted_payment_type: str = ""
    # SqlAgent가 추출한 keyword (SQL_SEARCH 전용)
    sql_keyword: str = ""
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
    embedder: Embeddings,
) -> list[Candidate]:
    """4채널 검색 + RRF + Hydration."""
    refined = intent_output.refined_query or query
    vec = await embedder.aembed_query(refined)

    # vector_search 는 metadata 에 payment_type 이 없으므로 post-filter 인자로 받지 않는다.
    # payment_type 은 hydration 직후 원본 컬럼으로 거른다(운영 hydration_node 와 동일 규칙).
    filters = dict(
        max_class_name=intent_output.max_class_name,
        area_name=intent_output.area_name,
        service_status=intent_output.service_status,
    )
    payment_type = intent_output.payment_type

    # Track A — identity, post-filter 적용
    try:
        a_rows = await vector_search(
            ai_session,
            vec,
            row_kind="identity",
            top_k=_EVAL_TOP_K,
            min_similarity=0.5,
            **filters,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_a 실패: %s", e)
        a_rows = []

    # Track B — summary, post-filter 미적용
    try:
        b_rows = await vector_search(
            ai_session,
            vec,
            row_kind="summary",
            top_k=_EVAL_TOP_K,
            min_similarity=0.5,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_b 실패: %s", e)
        b_rows = []

    # Track C — question, service_id별 dedup
    try:
        c_rows = await question_search(
            ai_session,
            vec,
            top_k=_EVAL_TOP_K,
            min_similarity=0.5,
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("track_c 실패: %s", e)
        c_rows = []

    # BM25 — bm25_search 파라미터는 limit (top_k 아님)
    tokens = tokenize_query(refined)
    try:
        d_rows = (
            await bm25_search(ai_session, tokens, limit=_EVAL_TOP_K) if tokens else []
        )
    except Exception as e:
        await ai_session.rollback()
        logger.warning("bm25 실패: %s", e)
        d_rows = []

    # 채널별 service_id 집합 (어느 채널에서 왔는지 추적)
    channel_hits: dict[str, set[str]] = {
        "track_a": {r["service_id"] for r in a_rows},
        "track_b": {r["service_id"] for r in b_rows},
        "track_c": {r["service_id"] for r in c_rows},
        "bm25": {r["service_id"] for r in d_rows},
    }

    merged = reciprocal_rank_fusion(
        {
            ch: [r["service_id"] for r in rows]
            for ch, rows in [
                ("track_a", a_rows),
                ("track_b", b_rows),
                ("track_c", c_rows),
                ("bm25", d_rows),
            ]
        },
        weights=None,  # 평가용 baseline: 비가중치
        k_constant=60,
    )

    service_ids = [sid for sid, _ in merged[:_EVAL_TOP_K]]
    rrf_scores = {sid: score for sid, score in merged}

    hydrated = await hydrate_services(data_session, service_ids)

    # payment post-filter (무료=정확/유료=접두) — 운영 hydration_node 와 동일 규칙.
    # recall 주의: merged[:_EVAL_TOP_K] 절단 "이후"에 필터하므로, 상위 후보가
    # 반대 결제유형으로 채워지면 후보 수가 _EVAL_TOP_K 보다 줄 수 있다(운영과 동일 동작).
    hydrated = _filter_by_payment(hydrated, payment_type)

    candidates = []
    for row in hydrated:
        sid = row["service_id"]
        contributed = [ch for ch, ids in channel_hits.items() if sid in ids]
        candidates.append(
            Candidate(
                service_id=sid,
                service_name=row.get("service_name", ""),
                area_name=row.get("area_name", ""),
                max_class_name=row.get("max_class_name", ""),
                service_status=row.get("service_status", ""),
                service_url=row.get("service_url", ""),
                channels=contributed,
                score=rrf_scores.get(sid, 0.0),
            )
        )
    return candidates


async def _run_sql(
    query: str,
    intent_output: _IntentOutput,
    *,
    data_session: AsyncSession,
    sql_agent: SqlAgent,
) -> tuple[list[Candidate], str]:
    """SQL 검색 — SqlAgent로 keyword·날짜까지 LLM 추출 후 sql_search 실행.

    refined_query(라우터가 벡터검색용으로 압축한 문자열)를 SqlAgent에 넘기면
    "시설"·"마포구" 같은 비관련 단어가 ILIKE keyword로 추출되어 0건이 된다.
    따라서 refined_query=None으로 SqlAgent가 원본 메시지에서 직접 추출하도록 한다.
    top_k는 _EVAL_TOP_K(20)를 사용해 운영 기본값(10)보다 많은 후보를 확보한다.

    Returns
    -------
    (candidates, sql_keyword): 후보 목록과 SqlAgent가 추출한 keyword 문자열
    """
    # TypedDict는 런타임에 강제되지 않으므로 필요한 키만 포함한 dict 전달.
    # refined_query=None → SqlAgent가 원본 message 기반으로 keyword·날짜만 추출.
    # 라우터 필터(max_class_name/area_name/service_status)는 SqlAgent가 원본에서 재추출.
    state: dict = {
        "message": query,
        "refined_query": None,
        "max_class_name": None,
        "area_name": None,
        "service_status": None,
        "payment_type": None,
    }
    result_state = await sql_agent.search(  # type: ignore[arg-type]
        state, data_session, top_k=_EVAL_TOP_K
    )
    rows: list[dict] = result_state.get("sql_results") or []
    sql_keyword: str = result_state.get("sql_keyword") or ""
    candidates = [
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
    return candidates, sql_keyword


async def run_query(
    query: str,
    intent_output: _IntentOutput,
    *,
    ai_session: AsyncSession,
    data_session: AsyncSession,
    embedder: Embeddings,
    sql_agent: SqlAgent,
) -> QueryResult:
    intent = intent_output.intent
    sql_keyword = ""

    if intent == IntentType.VECTOR_SEARCH:
        candidates = await _run_vector(
            query,
            intent_output,
            ai_session=ai_session,
            data_session=data_session,
            embedder=embedder,
        )
    elif intent == IntentType.SQL_SEARCH:
        candidates, sql_keyword = await _run_sql(
            query,
            intent_output,
            data_session=data_session,
            sql_agent=sql_agent,
        )
    else:
        candidates = []

    return QueryResult(
        query=query,
        intent=intent.value,
        sub_intent=intent_output.vector_sub_intent or "",
        refined_query=intent_output.refined_query or query,
        reasoning=getattr(intent_output, "reasoning", "") or "",
        extracted_max_class_name=intent_output.max_class_name or "",
        extracted_area_name=intent_output.area_name or "",
        extracted_service_status=intent_output.service_status or "",
        extracted_payment_type=intent_output.payment_type or "",
        sql_keyword=sql_keyword,
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# 출력 포맷
# ---------------------------------------------------------------------------


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def print_result(result: QueryResult) -> None:
    print()
    print(
        f"  Intent  : {result.intent}"
        + (f" / {result.sub_intent}" if result.sub_intent else "")
    )
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
    embedder: Embeddings,
    router: RouterAgent | None,
    sql_agent: SqlAgent,
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
            intent_str = (
                input("  intent (V=vector, S=sql, skip=fallback) [V]: ").strip().upper()
                or "V"
            )
            intent_map = {"V": IntentType.VECTOR_SEARCH, "S": IntentType.SQL_SEARCH}
            intent_output = _IntentOutput(
                intent=intent_map.get(intent_str, IntentType.VECTOR_SEARCH)
            )

        print(f"  검색 중... (intent={intent_output.intent.value})", end="\r")
        result = await run_query(
            query,
            intent_output,
            ai_session=ai_session,
            data_session=data_session,
            embedder=embedder,
            sql_agent=sql_agent,
        )
        print(f"  질의: {query}")
        print_result(result)

        if not result.candidates:
            records.append(
                {
                    "query": query,
                    "intent": result.intent,
                    "sub_intent": result.sub_intent,
                    "correct_service_ids": "",
                }
            )
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

        records.append(
            {
                "query": query,
                "intent": result.intent,
                "sub_intent": result.sub_intent,
                "correct_service_ids": ",".join(correct_ids),
            }
        )
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
    if mode == "a":
        import sys as _sys

        print(f"  [경고] {path} 에 추가 기록합니다 (기존 내용 유지).", file=_sys.stderr)
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
    embedder: Embeddings,
    router: RouterAgent | None,
    sql_agent: SqlAgent,
    output_path: Path,
) -> None:
    """queries_draft.tsv → candidates_review.tsv."""
    queries = _load_queries(queries_file)
    print(f"{len(queries)}개 질의 처리 중...")

    rows: list[dict] = []
    results_for_conditions: list[QueryResult] = []

    for i, (query, intent_hint, sub_intent_hint, _) in enumerate(queries, 1):
        print(f"  [{i:>3}/{len(queries)}] {query[:40]}")

        # 인텐트 분류:
        #   라우터가 있으면 항상 실행 — 필터(max_class_name/area_name/service_status)와
        #   refined_query를 추출하기 위해서다. intent_hint는 라우터 결과의 intent 필드를
        #   오버라이드하는 용도로만 사용한다 (평가셋의 ground-truth 레이블).
        #   --skip-router인 경우에만 hint로 전체를 대체한다.
        if router is not None:
            intent_output = await router.classify(query)
            # TSV hint로 intent 오버라이드 (필터/refined_query는 라우터 추출값 유지)
            if intent_hint and intent_hint in (t.value for t in IntentType):
                update: dict = {"intent": IntentType(intent_hint)}
                if sub_intent_hint:
                    update["vector_sub_intent"] = sub_intent_hint
                intent_output = intent_output.model_copy(update=update)
        elif intent_hint and intent_hint in (t.value for t in IntentType):
            intent_output = _IntentOutput(
                intent=IntentType(intent_hint),
                vector_sub_intent=sub_intent_hint or None,
            )
        else:
            intent_output = _IntentOutput(intent=IntentType.VECTOR_SEARCH)

        try:
            result = await run_query(
                query,
                intent_output,
                ai_session=ai_session,
                data_session=data_session,
                embedder=embedder,
                sql_agent=sql_agent,
            )
        except Exception as e:
            logger.error("질의 실패 [%s]: %s", query, e)
            result = QueryResult(
                query=query,
                intent=intent_output.intent.value,
                sub_intent=intent_output.vector_sub_intent or "",
                refined_query=intent_output.refined_query or query,
                reasoning=getattr(intent_output, "reasoning", "") or "",
                extracted_max_class_name=intent_output.max_class_name or "",
                extracted_area_name=intent_output.area_name or "",
                extracted_service_status=intent_output.service_status or "",
                extracted_payment_type=intent_output.payment_type or "",
                candidates=[],
            )

        if not result.candidates:
            rows.append(_candidate_row(result, None, rank=0))
        else:
            for rank, c in enumerate(result.candidates, 1):
                rows.append(_candidate_row(result, c, rank=rank))

        # 쿼리별 추출 조건 — candidates_review와 별개 파일로 분리 저장
        results_for_conditions.append(result)

    _write_candidates(rows, output_path)
    conditions_path = output_path.parent / "query_conditions.tsv"
    _write_query_conditions(results_for_conditions, conditions_path)
    print(f"\n완료: {len(rows)}행 → {output_path}")
    print(
        f"      {len(results_for_conditions)}건 → {conditions_path} (쿼리별 추출 조건)"
    )
    print("is_correct 컬럼(y/공백)을 채운 뒤 finalize_eval_set.py 를 실행하세요.")


def _candidate_row(result: QueryResult, c: Candidate | None, rank: int) -> dict:
    return {
        "query": result.query,
        "intent": result.intent,
        "sub_intent": result.sub_intent,
        "refined_query": result.refined_query,
        "rank": rank,
        "service_id": c.service_id if c else "",
        "service_name": c.service_name if c else "",
        "area_name": c.area_name if c else "",
        "max_class_name": c.max_class_name if c else "",
        "service_status": c.service_status if c else "",
        "channels": ",".join(c.channels) if c else "",
        "score": f"{c.score:.4f}" if c else "",
        "is_correct": "",  # 사람이 채움
    }


def _write_candidates(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "query",
        "intent",
        "sub_intent",
        "refined_query",
        "rank",
        "service_id",
        "service_name",
        "area_name",
        "max_class_name",
        "service_status",
        "channels",
        "score",
        "is_correct",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_query_conditions(results: list[QueryResult], path: Path) -> None:
    """쿼리별 라우터 추출 조건을 별도 TSV에 저장한다.

    candidates_review.tsv는 rank별 row가 반복되므로 쿼리 조건을 한 곳에서
    확인하기 어렵다. 본 파일은 쿼리당 1행만 가지며, 라우터의 reasoning과
    추출된 enum 값을 한눈에 비교할 수 있게 한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "query",
        "intent",
        "sub_intent",
        "refined_query",
        "reasoning",
        "extracted_max_class_name",
        "extracted_area_name",
        "extracted_service_status",
        "extracted_payment_type",
        "sql_keyword",
        "candidate_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "query": r.query,
                    "intent": r.intent,
                    "sub_intent": r.sub_intent,
                    "refined_query": r.refined_query,
                    "reasoning": r.reasoning,
                    "extracted_max_class_name": r.extracted_max_class_name,
                    "extracted_area_name": r.extracted_area_name,
                    "extracted_service_status": r.extracted_service_status,
                    "extracted_payment_type": r.extracted_payment_type,
                    "sql_keyword": r.sql_keyword,
                    "candidate_count": len(r.candidates),
                }
            )


def _load_queries(path: Path) -> list[tuple[str, str, str, str]]:
    """TSV에서 (query, intent, sub_intent, notes) 리스트 반환."""
    results = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            results.append(
                (
                    row.get("query", "").strip(),
                    row.get("intent", "").strip(),
                    row.get("sub_intent", "").strip(),
                    row.get("notes", "").strip(),
                )
            )
    return [(q, i, s, n) for q, i, s, n in results if q]


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
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

        router: RouterAgent | None = None
        if not args.skip_router:
            from llm.client import get_chat_model

            router = RouterAgent(model=get_chat_model())

        # SqlAgent는 항상 생성 (SQL 쿼리 keyword 추출용 LLM 체인 초기화만, 비용 없음)
        sql_agent = SqlAgent()

        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")

        _eval_dir = Path(__file__).resolve().parent  # scripts/eval/
        async with OnData() as data_session, OnAi() as ai_session:
            if args.mode == "interactive":
                output = args.output or _eval_dir / f"holdout_draft_{timestamp}.tsv"
                await interactive_loop(
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    router=router,
                    sql_agent=sql_agent,
                    output_path=Path(output),
                )
            else:
                queries_file = Path(args.batch)
                output = args.output or _eval_dir / f"candidates_review_{timestamp}.tsv"
                await batch_run(
                    queries_file,
                    ai_session=ai_session,
                    data_session=data_session,
                    embedder=embedder,
                    router=router,
                    sql_agent=sql_agent,
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
    mode.add_argument(
        "--interactive",
        dest="mode",
        action="store_const",
        const="interactive",
        help="REPL 방식: 직접 질의 입력 → 정답 선택",
    )
    mode.add_argument(
        "--batch",
        metavar="QUERIES_FILE",
        help="배치 방식: queries_draft.tsv 읽어 candidates_review.tsv 출력",
    )

    parser.add_argument(
        "--output",
        metavar="OUTPUT_FILE",
        help="출력 파일 경로 (기본값: 타임스탬프 자동 생성)",
    )
    parser.add_argument(
        "--skip-router",
        action="store_true",
        help="RouterAgent LLM 호출 생략 (벡터 검색 only)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\n중단됨")
        sys.exit(0)
