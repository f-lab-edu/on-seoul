"""구조화 메타데이터 추출기.

시설 정보에서 fee, operating_hours, cancellation, facilities,
capacity, restrictions, summary를 LLM으로 추출한다.
"""

import logging

from pydantic import BaseModel, Field

from llm.embedding_config import EXTRACTION_MAX_RETRIES, EXTRACTION_MIN_CHARS
from llm.prompts.extraction import (
    EXTRACTION_PROMPT_FULL,
    EXTRACTION_PROMPT_METADATA_ONLY,
)

logger = logging.getLogger(__name__)


class ExtractedMetadata(BaseModel):
    fee: str | None = Field(default=None)
    operating_hours: str | None = Field(default=None)
    cancellation: str | None = Field(default=None)
    facilities: list[str] = Field(default_factory=list)
    capacity: str | None = Field(default=None)
    restrictions: list[str] = Field(default_factory=list)
    summary: str = Field(description="시설 한 줄 요약 (Track B 임베딩 입력)")


async def extract_metadata(
    *,
    service_name: str,
    area_name: str | None = None,
    max_class_name: str | None = None,
    min_class_name: str | None = None,
    place_name: str | None = None,
    target_info: str | None = None,
    payment_type: str | None = None,
    cleaned_detail: str,
    llm_client,
) -> ExtractedMetadata | None:
    """시설 메타데이터 구조화 추출.

    cleaned_detail 길이 >= EXTRACTION_MIN_CHARS 이면 EXTRACTION_PROMPT_FULL 사용.
    짧거나 비어있으면 EXTRACTION_PROMPT_METADATA_ONLY 사용.
    EXTRACTION_MAX_RETRIES 회 재시도 후 실패하면 None 반환.
    """
    use_full = len(cleaned_detail) >= EXTRACTION_MIN_CHARS
    prompt = EXTRACTION_PROMPT_FULL if use_full else EXTRACTION_PROMPT_METADATA_ONLY

    chain = prompt | llm_client.with_structured_output(ExtractedMetadata)

    input_data = {
        "service_name": service_name,
        "area_name": area_name or "",
        "max_class_name": max_class_name or "",
        "min_class_name": min_class_name or "",
        "place_name": place_name or "",
        "target_info": target_info or "",
        "payment_type": payment_type or "",
        "cleaned_detail": cleaned_detail,
    }

    for attempt in range(EXTRACTION_MAX_RETRIES + 1):
        try:
            result = await chain.ainvoke(input_data)
            return result
        except Exception:
            if attempt < EXTRACTION_MAX_RETRIES:
                logger.warning(
                    "extract_metadata %d차 실패, 재시도 중", attempt + 1, exc_info=True
                )
            else:
                logger.error(
                    "extract_metadata %d차 실패, None 반환", attempt + 1, exc_info=True
                )

    return None
