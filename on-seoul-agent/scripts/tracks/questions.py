"""Track C: HyQE 예상질문 임베딩 적재."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from llm.hyqe import generate_questions
from scripts.tracks._shared import INSERT_ROW, ServiceRecord

logger = logging.getLogger(__name__)


async def embed_and_insert_questions(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder,
    llm_client,
    cleaned_detail: str,
    extracted_summary: str,
) -> bool:
    """Track C question 행을 임베딩하여 service_embeddings에 INSERT/UPSERT한다.

    generate_questions() 호출 후 각 질문을 row_kind='question', idx=i 로 적재한다.
    실패 시 False 반환.
    """
    questions = await generate_questions(
        service_name=service["service_name"],
        area_name=service.get("area_name"),
        max_class_name=service.get("max_class_name"),
        min_class_name=service.get("min_class_name"),
        cleaned_detail=cleaned_detail,
        extracted_summary=extracted_summary,
        llm_client=llm_client,
    )

    if not questions:
        logger.warning(
            "generate_questions 결과 없음: service_id=%s", service["service_id"]
        )
        return False

    for idx, question in enumerate(questions):
        vector = await embedder.aembed_query(question.question_text)
        await session.execute(
            INSERT_ROW,
            {
                "service_id": service["service_id"],
                "row_kind": "question",
                "idx": idx,
                "service_name": service["service_name"],
                "embedding_text": question.question_text,
                "embedding": str(vector),
                "metadata": None,
                "intent_label": question.intent_label,
            },
        )

    return True
