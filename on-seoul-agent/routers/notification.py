"""알림 요약/하이라이트 생성 라우터.

POST /notification/template — 조건 기반 구독에 매칭된 여러 서비스 변경(서비스
그룹 리스트)을 받아, LLM으로 짧은 요약/하이라이트(title/summary)를 생성한다.

역할: 서비스명·상태·접수기간·링크 같은 "사실"은 소비자(API 서비스)의 Knock
이메일 Liquid 템플릿이 결정적으로 렌더링한다. 이 엔드포인트는 사실 재나열이
아니라 "무엇을 주목할지"만 담은 요약을 생성한다. title·summary 모두 LLM이 생성.

LLM 실패(타임아웃, 예외, 빈 응답) 시 503을 반환한다(소비자가 자체 fallback 사용).
망 분리/Nginx 레벨에서 보호한다고 가정하므로 별도 인증은 두지 않는다.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from llm.client import get_chat_model
from llm.prompts.notification import NOTIFICATION_FEW_SHOT_EXAMPLES, NOTIFICATION_SYSTEM
from schemas.notification import (
    NotificationTemplateRequest,
    NotificationTemplateResponse,
    ServiceChangeGroup,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notification", tags=["notification"])

_LLM_TIMEOUT_SECONDS = 8.0  # 소비자(10초)보다 짧게 self-timeout
_503_DETAIL = "알림 템플릿 생성에 실패했습니다."


class _SummaryResponse(BaseModel):
    """LLM 구조화 출력 전용 스키마. title·summary 모두 LLM이 생성한다."""

    title: str
    summary: str


# image_url은 메시지 생성에 직접 쓰지 않으므로 생략한다.
_META_FIELDS = (
    ("service_name", "service_name"),
    ("service_url", "service_url"),
    ("place_name", "place_name"),
    ("area_name", "area_name"),
    ("service_status", "service_status"),
    ("target_info", "target_info"),
    ("receipt_start_dt", "receipt_start_dt"),
    ("receipt_end_dt", "receipt_end_dt"),
)


def _format_group(index: int, group: ServiceChangeGroup) -> str:
    """서비스 그룹 1개를 LLM 입력용 블록 텍스트로 변환한다."""
    # service_id는 LLM 입력에 절대 넣지 않는다(출력 누출 구조적 차단).
    # 서비스 구분은 [서비스 N] 인덱스만으로 충분하다.
    lines = [f"[서비스 {index}]"]
    for label, attr in _META_FIELDS:
        value = getattr(group, attr)
        if value:
            lines.append(f"- {label}: {value}")
    lines.append("- 변경:")
    for c in group.changes:
        parts = [c.change_type]
        if c.field_name:
            parts.append(c.field_name)
        detail = " ".join(parts)
        if c.old_value is not None or c.new_value is not None:
            old = c.old_value if c.old_value is not None else "null"
            new = c.new_value if c.new_value is not None else "null"
            detail += f": {old} -> {new}"
        lines.append(f"  - {detail}")
    return "\n".join(lines)


def _format_services(req: NotificationTemplateRequest) -> str:
    """전체 서비스 그룹을 LLM 입력 텍스트로 직렬화한다."""
    return "\n".join(
        _format_group(i, group) for i, group in enumerate(req.services, start=1)
    )


def _build_messages(
    req: NotificationTemplateRequest,
) -> list[SystemMessage | HumanMessage | AIMessage]:
    """시스템 프롬프트 + few-shot + 실제 입력 메시지 목록을 구성한다."""
    messages: list[SystemMessage | HumanMessage | AIMessage] = [
        SystemMessage(content=NOTIFICATION_SYSTEM)
    ]
    for example in NOTIFICATION_FEW_SHOT_EXAMPLES:
        messages.append(HumanMessage(content=example["input"]))
        messages.append(AIMessage(content=example["output"]))
    messages.append(HumanMessage(content=_format_services(req)))
    return messages


async def _invoke_llm(req: NotificationTemplateRequest) -> _SummaryResponse:
    """LLM을 호출해 title/summary를 반환한다.

    Gemini API 최솟값(10s) 제약으로 SDK timeout을 self-timeout(8s) 이하로
    설정할 수 없다. SDK timeout은 기본값(30s)을 사용하고 asyncio.wait_for가
    consumer 계약(10s)을 보장한다. max_retries=1로 알림 경로 재시도는 최소화.
    """
    llm = get_chat_model(temperature=0.2, max_retries=1)
    chain = llm.with_structured_output(_SummaryResponse)
    return await chain.ainvoke(_build_messages(req))


@router.post("/template")
async def create_template(
    req: NotificationTemplateRequest,
) -> NotificationTemplateResponse:
    """서비스 그룹 리스트를 받아 알림 title/summary를 생성한다.

    - title/summary: LLM 생성. self-timeout 8초.
    - 빈 title 또는 빈 summary → 503.
    - LLM 실패 degrade: 모든 예외를 503으로 반환.
    """
    service_ids = ",".join(g.service_id for g in req.services)
    try:
        result: _SummaryResponse = await asyncio.wait_for(
            _invoke_llm(req),
            timeout=_LLM_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "알림 템플릿 LLM 타임아웃 (%.1fs 초과). service_ids=%s",
            _LLM_TIMEOUT_SECONDS,
            service_ids,
        )
        raise HTTPException(status_code=503, detail=_503_DETAIL)
    except Exception:
        logger.exception("알림 템플릿 LLM 호출 실패. service_ids=%s", service_ids)
        raise HTTPException(status_code=503, detail=_503_DETAIL)

    if not result.title.strip() or not result.summary.strip():
        logger.warning(
            "알림 템플릿 LLM이 빈 title/summary 반환. service_ids=%s "
            "title=%r summary=%r",
            service_ids,
            result.title,
            result.summary,
        )
        raise HTTPException(status_code=503, detail=_503_DETAIL)

    return NotificationTemplateResponse(
        title=result.title,
        summary=result.summary,
    )
