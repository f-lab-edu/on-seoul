"""Track A: 시설 식별 임베딩 적재."""

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.tracks._shared import INSERT_ROW, ServiceRecord


def build_live_metadata(service: ServiceRecord) -> dict:
    """identity metadata 중 원본 테이블에서 그대로 오는(자주 변하는) 필드.

    tools/vector_search.py 가 metadata->>'service_status' / 'max_class_name' /
    'area_name' 을 pre-filter 로 쓰므로 재임베딩을 건너뛰더라도 이 값들은 최신이어야 한다.
    """
    return {
        "service_gubun": service.get("service_gubun"),
        "area_name": service.get("area_name"),
        "max_class_name": service.get("max_class_name"),
        "min_class_name": service.get("min_class_name"),
        "place_name": service.get("place_name"),
        "service_status": service.get("service_status"),
        "payment_type": service.get("payment_type"),
        "target_info": service.get("target_info"),
        "service_url": service.get("service_url"),
        "receipt_start_dt": str(service["receipt_start_dt"])
        if service.get("receipt_start_dt")
        else None,
        "receipt_end_dt": str(service["receipt_end_dt"])
        if service.get("receipt_end_dt")
        else None,
        "service_open_start_dt": str(service["service_open_start_dt"])
        if service.get("service_open_start_dt")
        else None,
        "service_open_end_dt": str(service["service_open_end_dt"])
        if service.get("service_open_end_dt")
        else None,
        "coord_x": float(service["coord_x"])
        if service.get("coord_x") is not None
        else None,
        "coord_y": float(service["coord_y"])
        if service.get("coord_y") is not None
        else None,
    }


_UPDATE_METADATA = text("""
    UPDATE service_embeddings
    SET metadata = COALESCE(metadata, CAST('{}' AS jsonb)) || CAST(:patch AS jsonb),
        updated_at = NOW()
    WHERE service_id = :service_id
      AND row_kind = 'identity'
      AND idx = 0
""")


async def update_identity_metadata(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    source_hash: str,
) -> None:
    """재임베딩 없이 identity 행의 metadata 만 최신 값으로 갱신한다.

    jsonb shallow merge 라 LLM 추출 결과('extracted')는 보존된다.
    """
    patch = {**build_live_metadata(service), "source_hash": source_hash}
    await session.execute(
        _UPDATE_METADATA,
        {
            "service_id": service["service_id"],
            "patch": json.dumps(patch, ensure_ascii=False),
        },
    )


async def embed_and_insert_identity(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder,
    extracted,
) -> None:
    """Track A identity 행을 임베딩하여 service_embeddings에 INSERT/UPSERT한다.

    embedding_text: "{area_name} {max_class_name} {min_class_name} {service_name} {place_name}"
    row_kind='identity', idx=0
    metadata: service 필드 + extracted.model_dump()
    (source_hash 는 전체 트랙 적재가 모두 성공한 뒤 update_identity_metadata 로 찍는다)
    """
    parts = [
        service.get("area_name") or "",
        service.get("max_class_name") or "",
        service.get("min_class_name") or "",
        service.get("service_name") or "",
        service.get("place_name") or "",
    ]
    embedding_text = " ".join(p for p in parts if p.strip()).strip()

    vector = await embedder.aembed_query(embedding_text)

    metadata = {
        **build_live_metadata(service),
        "extracted": extracted.model_dump() if extracted is not None else None,
    }

    await session.execute(
        INSERT_ROW,
        {
            "service_id": service["service_id"],
            "row_kind": "identity",
            "idx": 0,
            "service_name": service["service_name"],
            "embedding_text": embedding_text,
            "embedding": str(vector),
            "metadata": json.dumps(metadata, ensure_ascii=False),
            "intent_label": None,
        },
    )
