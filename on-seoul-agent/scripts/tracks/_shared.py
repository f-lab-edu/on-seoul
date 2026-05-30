"""트랙 모듈 공유 타입 및 SQL 템플릿."""

from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INSERT_ROW = text("""
    INSERT INTO service_embeddings (
        service_id, row_kind, idx,
        service_name, embedding_text, embedding,
        metadata, intent_label
    ) VALUES (
        :service_id, :row_kind, :idx,
        :service_name, :embedding_text, CAST(:embedding AS vector),
        CAST(:metadata AS jsonb), :intent_label
    )
    ON CONFLICT (service_id, row_kind, idx) DO UPDATE SET
        service_name = EXCLUDED.service_name,
        embedding_text = EXCLUDED.embedding_text,
        embedding = EXCLUDED.embedding,
        metadata = EXCLUDED.metadata,
        intent_label = EXCLUDED.intent_label,
        updated_at = NOW()
""")


class ServiceRecord(TypedDict, total=False):
    service_id: str
    service_name: str
    service_gubun: str | None
    area_name: str | None
    max_class_name: str | None
    min_class_name: str | None
    place_name: str | None
    target_info: str | None
    payment_type: str | None
    detail_content: str | None
    service_status: str | None
    service_url: str | None
    receipt_start_dt: object
    receipt_end_dt: object
    service_open_start_dt: object
    service_open_end_dt: object
    coord_x: float | None
    coord_y: float | None


async def delete_rows_by_service_id(
    session: AsyncSession,
    service_id: str,
    *,
    tracks: set[str],
) -> None:
    """tracks에 해당하는 row_kind 행을 삭제한다."""
    track_to_kind: dict[str, str] = {
        "A": "identity",
        "B": "summary",
        "C": "question",
    }
    row_kinds = [track_to_kind[t] for t in tracks if t in track_to_kind]
    if not row_kinds:
        return

    placeholders = ", ".join(f":kind_{i}" for i in range(len(row_kinds)))
    bind: dict = {"service_id": service_id}
    for i, kind in enumerate(row_kinds):
        bind[f"kind_{i}"] = kind

    await session.execute(
        text(f"""
            DELETE FROM service_embeddings
            WHERE service_id = :service_id
              AND row_kind IN ({placeholders})
        """),
        bind,
    )
