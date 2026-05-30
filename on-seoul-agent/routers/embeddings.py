"""임베딩 동기화 라우터.

POST /embeddings/services/sync — 백그라운드로 upsert/delete 작업을 실행하고
202 Accepted를 즉시 반환한다.
"""

import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.config import settings
from llm.client import get_chat_model, get_embeddings
from schemas.embeddings import (
    ServiceEmbeddingsSyncRequest,
    ServiceEmbeddingsSyncResponse,
)
from scripts.embed_metadata import process_service
from scripts.tracks._shared import ServiceRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.post("/services/sync", status_code=202)
async def services_sync(
    req: ServiceEmbeddingsSyncRequest,
    background: BackgroundTasks,
) -> ServiceEmbeddingsSyncResponse:
    """임베딩 동기화 요청을 백그라운드로 처리하고 202를 반환한다."""
    background.add_task(_run_services_sync, req.upsert, req.delete)
    return ServiceEmbeddingsSyncResponse(
        accepted={"upsert": len(req.upsert), "delete": len(req.delete)}
    )


async def _run_services_sync(upsert: list[str], delete: list[str]) -> None:
    """백그라운드 임베딩 동기화 실행."""
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
        sem = asyncio.Semaphore(settings.embedding_sync_concurrency)

        # delete 처리
        if delete:
            async with OnAiSession() as ai_session:
                async with ai_session.begin():
                    for sid in delete:
                        await ai_session.execute(
                            text(
                                "DELETE FROM service_embeddings WHERE service_id = :sid"
                            ),
                            {"sid": sid},
                        )
            logger.info("임베딩 삭제 완료: %d건", len(delete))

        # upsert 처리
        if upsert:

            async def _upsert_one(service_id: str) -> None:
                async with sem:
                    async with OnDataSession() as data_session:
                        row = await _fetch_service_row(data_session, service_id)
                    if row is None:
                        logger.warning("service_id 조회 실패 (삭제됨?): %s", service_id)
                        return
                    async with OnAiSession() as ai_session:
                        try:
                            await process_service(
                                row,
                                session=ai_session,
                                embedder=embedder,
                                llm_client=llm_client,
                                tracks={"A", "B", "C"},
                            )
                        except Exception:
                            logger.exception(
                                "임베딩 처리 실패: service_id=%s", service_id
                            )

            await asyncio.gather(*[_upsert_one(sid) for sid in upsert])
            logger.info("임베딩 upsert 완료: %d건", len(upsert))

    finally:
        await on_data_engine.dispose()
        await on_ai_engine.dispose()


async def _fetch_service_row(session, service_id: str) -> ServiceRecord | None:
    """on_data.public_service_reservations에서 단일 시설을 조회한다."""
    result = await session.execute(
        text("""
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
            WHERE service_id = :service_id
              AND deleted_at IS NULL
        """),
        {"service_id": service_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    keys = result.keys()
    return dict(zip(keys, row))  # type: ignore[return-value]
