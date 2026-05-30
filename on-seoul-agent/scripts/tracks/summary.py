"""Track B: 추출 요약 임베딩 적재."""

from sqlalchemy.ext.asyncio import AsyncSession

from scripts.tracks._shared import INSERT_ROW, ServiceRecord


async def embed_and_insert_summary(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder,
    extracted,
) -> None:
    """Track B summary 행을 임베딩하여 service_embeddings에 INSERT/UPSERT한다.

    embedding_text: extracted.summary
    row_kind='summary', idx=0
    metadata=None, intent_label=None
    """
    embedding_text = extracted.summary

    vector = await embedder.aembed_query(embedding_text)

    await session.execute(
        INSERT_ROW,
        {
            "service_id": service["service_id"],
            "row_kind": "summary",
            "idx": 0,
            "service_name": service["service_name"],
            "embedding_text": embedding_text,
            "embedding": str(vector),
            "metadata": None,
            "intent_label": None,
        },
    )
