"""트랙 모듈 공유 타입 및 SQL 템플릿."""

import hashlib
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


# 임베딩 결과를 좌우하는 입력 필드. 여기 없는 필드(service_status, receipt_*_dt,
# coord_*, service_url 등)는 임베딩 텍스트/LLM 입력에 쓰이지 않으므로 해시에서 제외한다.
SOURCE_HASH_FIELDS: tuple[str, ...] = (
    "service_name",
    "area_name",
    "max_class_name",
    "min_class_name",
    "place_name",
    "target_info",
    "payment_type",
)

_FIELD_SEP = "\x1e"
_KV_SEP = "\x1f"


def compute_source_hash(service: ServiceRecord, cleaned_detail: str) -> str:
    """임베딩 소스 필드 + 정제된 상세내용으로 안정적인 해시를 만든다.

    정규화: None 과 빈 문자열/공백은 동일 값으로 취급(strip 후 비교).
    직렬화: "필드명\x1f값" 을 "\x1e" 로 이어 붙여 필드 경계를 모호하지 않게 한다.
    detail_content 는 원문이 아니라 clean_detail_content() 결과를 받아야
    boilerplate/공백만 바뀐 변경이 재생성을 유발하지 않는다.
    """
    pairs = [(f, service.get(f)) for f in SOURCE_HASH_FIELDS]
    pairs.append(("cleaned_detail", cleaned_detail))
    payload = _FIELD_SEP.join(
        f"{name}{_KV_SEP}{'' if value is None else str(value).strip()}"
        for name, value in pairs
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def fetch_source_hash(session: AsyncSession, service_id: str) -> str | None:
    """identity 행 metadata에 저장된 source_hash를 읽는다. 없으면 None."""
    result = await session.execute(
        text("""
            SELECT metadata->>'source_hash'
            FROM service_embeddings
            WHERE service_id = :service_id
              AND row_kind = 'identity'
              AND idx = 0
        """),
        {"service_id": service_id},
    )
    row = result.fetchone()
    return row[0] if row is not None else None
