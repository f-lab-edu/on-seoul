"""시설 메타데이터 Triple-Track 임베딩 배치 적재 스크립트.

on_data.public_service_reservations 에서 시설 데이터를 읽어
Triple-Track(A/B/C)으로 임베딩 벡터를 생성하고 on_ai.service_embeddings에 적재한다.

트랙 구성:
  Track A (identity): 시설 식별 텍스트 임베딩. 항상 생성.
  Track B (summary):  LLM 추출 요약 임베딩. LLM 성공 시 생성.
  Track C (questions): HyQE 예상질문 임베딩. LLM 성공 시 생성.

사용법
------
# seed 모드 (100건, 기본값)
uv run python scripts/embed_metadata.py

# 전량 적재
uv run python scripts/embed_metadata.py --all

# 건수 지정
uv run python scripts/embed_metadata.py --limit 500

# 증분 적재 (service_embeddings에 없는 service_id만)
uv run python scripts/embed_metadata.py --incremental

# 특정 트랙만 적재 (복수 지정 가능)
uv run python scripts/embed_metadata.py --track A
uv run python scripts/embed_metadata.py --track B
uv run python scripts/embed_metadata.py --track A B  # A + B 동시

# extraction_failed.tsv 재처리
uv run python scripts/embed_metadata.py --retry-failed

# dry-run
uv run python scripts/embed_metadata.py --dry-run --all
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from llm.client import get_chat_model, get_embeddings
from llm.extractor import extract_metadata
from scripts.cleaning.detail_content import clean_detail_content
from scripts.tracks._shared import ServiceRecord, delete_rows_by_service_id
from scripts.tracks.identity import embed_and_insert_identity
from scripts.tracks.questions import embed_and_insert_questions
from scripts.tracks.summary import embed_and_insert_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_EXTRACTION_FAILED_FILE = Path(__file__).parent / "extraction_failed.tsv"


async def process_service(
    service: ServiceRecord,
    *,
    session: AsyncSession,
    embedder,
    llm_client,
    tracks: set[str],
    extraction_failed_path: Path | None = None,
) -> None:
    """단일 시설의 Triple-Track 임베딩 적재.

    tracks: {"A"}, {"B"}, {"C"}, {"A","B","C"} 등 조합 가능.
    extraction_failed_path: LLM 추출 실패 시 service_id를 append할 파일.
    """
    cleaned = clean_detail_content(service.get("detail_content"))

    async with session.begin():
        await delete_rows_by_service_id(session, service["service_id"], tracks=tracks)

        extracted = None
        if tracks & {"A", "B", "C"}:
            extracted = await extract_metadata(
                service_name=service["service_name"],
                area_name=service.get("area_name"),
                max_class_name=service.get("max_class_name"),
                min_class_name=service.get("min_class_name"),
                place_name=service.get("place_name"),
                target_info=service.get("target_info"),
                payment_type=service.get("payment_type"),
                cleaned_detail=cleaned,
                llm_client=llm_client,
            )

        if "A" in tracks:
            await embed_and_insert_identity(
                session, service, embedder=embedder, extracted=extracted
            )

        if extracted is None:
            if extraction_failed_path:
                with extraction_failed_path.open("a", encoding="utf-8") as f:
                    f.write(f"{service['service_id']}\n")
            return

        if "B" in tracks:
            await embed_and_insert_summary(
                session, service, embedder=embedder, extracted=extracted
            )

        if "C" in tracks:
            await embed_and_insert_questions(
                session, service,
                embedder=embedder,
                llm_client=llm_client,
                cleaned_detail=cleaned,
                extracted_summary=extracted.summary,
            )


async def run(
    limit: int | None,
    incremental: bool = False,
    tracks: set[str] | None = None,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> None:
    effective_tracks = tracks or {"A", "B", "C"}

    on_data_engine = create_async_engine(settings.on_data_database_url, echo=False)
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )
    try:
        OnDataSession = async_sessionmaker(on_data_engine, expire_on_commit=False)
        OnAiSession = async_sessionmaker(on_ai_engine, expire_on_commit=False)

        embedder = get_embeddings()
        llm_client = get_chat_model()

        async with OnDataSession() as data_session:
            if retry_failed:
                rows = await _fetch_failed_rows(data_session)
            else:
                rows = await _fetch_rows(data_session, limit)

        if not rows:
            logger.info("적재할 데이터가 없습니다.")
            return

        if incremental and not retry_failed:
            async with OnAiSession() as ai_session:
                existing_ids = await _fetch_existing_service_ids(ai_session)
            before_count = len(rows)
            rows = [r for r in rows if r["service_id"] not in existing_ids]
            logger.info("기존 %d건 제외, %d건 신규 임베딩", before_count - len(rows), len(rows))
            if not rows:
                logger.info("신규 데이터가 없습니다.")
                return

        if dry_run:
            logger.info("[DRY-RUN] 총 %d건 처리 예정 (실제 적재 없음)", len(rows))
            return

        logger.info("총 %d건 처리 시작 (트랙: %s)", len(rows), sorted(effective_tracks))

        failed_path = _EXTRACTION_FAILED_FILE if "B" in effective_tracks or "C" in effective_tracks else None

        for i, row in enumerate(rows, start=1):
            async with OnAiSession() as ai_session:
                try:
                    await process_service(
                        row,
                        session=ai_session,
                        embedder=embedder,
                        llm_client=llm_client,
                        tracks=effective_tracks,
                        extraction_failed_path=failed_path,
                    )
                except Exception:
                    logger.exception("처리 실패: service_id=%s", row.get("service_id"))
            if i % 20 == 0 or i == len(rows):
                logger.info("진행: %d / %d", i, len(rows))

        logger.info("완료: %d건 처리", len(rows))
    finally:
        await on_data_engine.dispose()
        await on_ai_engine.dispose()


async def _fetch_existing_service_ids(session: AsyncSession) -> set[str]:
    """on_ai.service_embeddings에서 이미 적재된 service_id 집합을 조회한다."""
    result = await session.execute(
        text("SELECT DISTINCT service_id FROM service_embeddings")
    )
    return {row[0] for row in result.fetchall()}


async def _fetch_rows(session: AsyncSession, limit: int | None) -> list[dict]:
    """on_data.public_service_reservations 에서 소프트 삭제되지 않은 행을 조회한다."""
    _BASE_SQL = """
        SELECT
            service_id, service_name, service_gubun,
            max_class_name, min_class_name,
            area_name, place_name,
            service_status, payment_type, target_info,
            service_url, detail_content,
            receipt_start_dt, receipt_end_dt,
            service_open_start_dt, service_open_end_dt,
            coord_x, coord_y
        FROM public_service_reservations
        WHERE deleted_at IS NULL
        ORDER BY id
    """
    bind: dict = {}
    if limit is not None:
        sql = text(_BASE_SQL + " LIMIT :limit")
        bind["limit"] = limit
    else:
        sql = text(_BASE_SQL)

    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]


async def _fetch_failed_rows(session: AsyncSession) -> list[dict]:
    """extraction_failed.tsv에 기록된 service_id들을 재조회한다."""
    if not _EXTRACTION_FAILED_FILE.exists():
        logger.info("extraction_failed.tsv 파일이 없습니다.")
        return []

    service_ids = []
    with _EXTRACTION_FAILED_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            sid = line.strip()
            if sid:
                service_ids.append(sid)

    if not service_ids:
        return []

    placeholders = ", ".join(f":sid_{i}" for i in range(len(service_ids)))
    bind = {f"sid_{i}": sid for i, sid in enumerate(service_ids)}
    sql = text(f"""
        SELECT
            service_id, service_name, service_gubun,
            max_class_name, min_class_name,
            area_name, place_name,
            service_status, payment_type, target_info,
            service_url, detail_content,
            receipt_start_dt, receipt_end_dt,
            service_open_start_dt, service_open_end_dt,
            coord_x, coord_y
        FROM public_service_reservations
        WHERE service_id IN ({placeholders})
          AND deleted_at IS NULL
    """)
    result = await session.execute(sql, bind)
    keys = result.keys()
    return [dict(zip(keys, row)) for row in result.fetchall()]


# ---------------------------------------------------------------------------
# 하위 호환성: 기존 테스트가 참조하는 내부 심볼 유지
# ---------------------------------------------------------------------------

async def _fetch_existing_service_ids(session: AsyncSession) -> set[str]:  # noqa: F811
    result = await session.execute(
        text("SELECT DISTINCT service_id FROM service_embeddings")
    )
    return {row[0] for row in result.fetchall()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시설 메타데이터 Triple-Track 임베딩 적재")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="전량 적재 (기본값: seed 100건)")
    group.add_argument("--limit", type=int, default=None, help="적재할 최대 건수")

    parser.add_argument("--incremental", action="store_true", help="신규 service_id만 임베딩")
    parser.add_argument(
        "--track",
        nargs="+",
        choices=["A", "B", "C"],
        default=["A", "B", "C"],
        metavar="TRACK",
        help="적재할 트랙 (A B C 중 하나 이상, 기본값: A B C). 예: --track A B",
    )
    parser.add_argument("--retry-failed", action="store_true", help="extraction_failed.tsv 재처리")
    parser.add_argument("--dry-run", action="store_true", help="실제 적재 없이 대상 건수만 확인")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.all or args.incremental or args.retry_failed:
        limit = None
    elif args.limit is not None:
        limit = args.limit
    else:
        limit = 100

    tracks: set[str] = set(args.track)

    try:
        asyncio.run(
            run(
                limit,
                incremental=args.incremental,
                tracks=tracks,
                dry_run=args.dry_run,
                retry_failed=args.retry_failed,
            )
        )
    except Exception:
        sys.exit(1)
