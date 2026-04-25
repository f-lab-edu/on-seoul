"""시설 메타데이터 임베딩 배치 적재 스크립트.

on_data.public_service_reservations 에서 시설 데이터를 읽어
텍스트 문서로 변환한 뒤 Gemini 임베딩 벡터를 생성하고,
on_ai.service_embeddings 에 upsert한다.

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

# 전량 + 증분 조합 (전체 조회 후 기존 제외)
uv run python scripts/embed_metadata.py --all --incremental
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 실행 시 패키지 임포트를 위해)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import settings
from llm.client import get_embeddings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 문서 구성 전략
# 시설명 · 대분류 · 소분류 · 지역 · 대상 · 서비스 기간 · 상세 설명을 조합한다.
# 검색 품질 향상을 위해 공백 필드는 제외하고 구분자(|)로 연결한다.
# ---------------------------------------------------------------------------
_DETAIL_MAX_CHARS = 300  # 상세 설명 최대 길이 (토큰 절약)


def _build_document(row: dict) -> str:
    """DB 행 → 임베딩용 텍스트 문서 변환."""
    parts = [
        row.get("service_name") or "",
        row.get("max_class_name") or "",
        row.get("min_class_name") or "",
        row.get("area_name") or "",
        row.get("place_name") or "",
        row.get("target_info") or "",
        (row.get("detail_content") or "")[:_DETAIL_MAX_CHARS],
    ]
    return " | ".join(p for p in parts if p.strip())


def _build_metadata(row: dict) -> dict:
    """검색 결과 카드 렌더링에 필요한 필드를 JSONB metadata로 구성한다."""
    return {
        "service_gubun": row.get("service_gubun"),
        "max_class_name": row.get("max_class_name"),
        "min_class_name": row.get("min_class_name"),
        "area_name": row.get("area_name"),
        "place_name": row.get("place_name"),
        "service_status": row.get("service_status"),
        "payment_type": row.get("payment_type"),
        "target_info": row.get("target_info"),
        "service_url": row.get("service_url"),
        "receipt_start_dt": str(row["receipt_start_dt"]) if row.get("receipt_start_dt") else None,
        "receipt_end_dt": str(row["receipt_end_dt"]) if row.get("receipt_end_dt") else None,
        "service_open_start_dt": str(row["service_open_start_dt"]) if row.get("service_open_start_dt") else None,
        "service_open_end_dt": str(row["service_open_end_dt"]) if row.get("service_open_end_dt") else None,
        "coord_x": float(row["coord_x"]) if row.get("coord_x") is not None else None,
        "coord_y": float(row["coord_y"]) if row.get("coord_y") is not None else None,
    }


# ---------------------------------------------------------------------------
# 메인 로직
# ---------------------------------------------------------------------------

_BATCH_SIZE = 20  # 임베딩 API 병렬 호출 단위 (rate limit 고려)


async def run(limit: int | None, incremental: bool = False) -> None:
    on_data_engine = create_async_engine(settings.on_data_database_url, echo=False)
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},  # pgvector + asyncpg prepared statement 호환성
    )
    OnDataSession = async_sessionmaker(on_data_engine, expire_on_commit=False)
    OnAiSession = async_sessionmaker(on_ai_engine, expire_on_commit=False)

    embeddings = get_embeddings()

    async with OnDataSession() as data_session:
        rows = await _fetch_rows(data_session, limit)

    if not rows:
        logger.info("적재할 데이터가 없습니다.")
        return

    if incremental:
        async with OnAiSession() as ai_session:
            existing_ids = await _fetch_existing_service_ids(ai_session)

        before_count = len(rows)
        rows = [r for r in rows if r["service_id"] not in existing_ids]
        excluded_count = before_count - len(rows)
        logger.info("기존 %d건 제외, %d건 신규 임베딩", excluded_count, len(rows))

        if not rows:
            logger.info("신규 데이터가 없습니다.")
            return

    logger.info("총 %d건 처리 시작", len(rows))

    async with OnAiSession() as ai_session:
        for batch_start in range(0, len(rows), _BATCH_SIZE):
            batch = rows[batch_start : batch_start + _BATCH_SIZE]
            documents = [_build_document(r) for r in batch]

            vectors = await embeddings.aembed_documents(documents)

            await _upsert_batch(ai_session, batch, vectors)
            await ai_session.commit()

            logger.info(
                "진행: %d / %d",
                min(batch_start + _BATCH_SIZE, len(rows)),
                len(rows),
            )

    logger.info("완료: %d건 적재", len(rows))
    await on_data_engine.dispose()
    await on_ai_engine.dispose()


async def _fetch_existing_service_ids(session: AsyncSession) -> set[str]:
    """on_ai.service_embeddings에서 이미 적재된 service_id 집합을 조회한다."""
    result = await session.execute(text("SELECT service_id FROM service_embeddings"))
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


async def _upsert_batch(
    session: AsyncSession,
    rows: list[dict],
    vectors: list[list[float]],
) -> None:
    """service_embeddings에 ON CONFLICT(service_id) DO UPDATE upsert."""
    for row, vector in zip(rows, vectors):
        metadata = _build_metadata(row)
        await session.execute(
            text("""
                INSERT INTO service_embeddings
                    (service_id, service_name, metadata, embedding, updated_at)
                VALUES
                    (:service_id, :service_name, CAST(:metadata AS jsonb), CAST(:embedding AS vector), NOW())
                ON CONFLICT (service_id) DO UPDATE SET
                    service_name = EXCLUDED.service_name,
                    metadata     = EXCLUDED.metadata,
                    embedding    = EXCLUDED.embedding,
                    updated_at   = EXCLUDED.updated_at
            """),
            {
                "service_id": row["service_id"],
                "service_name": row["service_name"],
                "metadata": json.dumps(metadata, ensure_ascii=False),
                "embedding": str(vector),
            },
        )


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시설 메타데이터 임베딩 적재")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        action="store_true",
        help="전량 적재 (기본값: seed 100건)",
    )
    group.add_argument(
        "--limit",
        type=int,
        default=None,
        help="적재할 최대 건수",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="service_embeddings에 없는 service_id만 임베딩 (신규 데이터 전용)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.all:
        limit = None
    elif args.limit is not None:
        limit = args.limit
    else:
        limit = 100  # seed 기본값

    asyncio.run(run(limit, incremental=args.incremental))
