"""알림 템플릿 생성 라우터.

POST /notification/template — 조건 기반 구독에 매칭된 여러 서비스 변경(서비스
그룹 리스트)을 받아, LLM으로 푸시 알림 title/body를 하나로 묶어 생성한다.
LLM 실패(타임아웃, 예외, 빈 응답) 시 503을 반환한다(소비자가 자체 fallback 사용).

망 분리/Nginx 레벨에서 보호한다고 가정하므로 별도 인증은 두지 않는다.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
    header_name = group.service_name or group.service_id
    lines = [f"[서비스 {index}] {header_name}"]
    for label, attr in _META_FIELDS:
        if attr == "service_name":
            continue  # 헤더에 이미 표기
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


async def _invoke_llm(req: NotificationTemplateRequest) -> NotificationTemplateResponse:
    """LLM을 호출하여 NotificationTemplateResponse를 반환한다."""
    llm = get_chat_model(temperature=0.2)
    chain = llm.with_structured_output(NotificationTemplateResponse)
    return await chain.ainvoke(_build_messages(req))


@router.post("/template")
async def create_template(
    req: NotificationTemplateRequest,
) -> NotificationTemplateResponse:
    """서비스 그룹 리스트를 받아 알림 title/body를 생성한다.

    - self-timeout 8초: asyncio.wait_for로 클라이언트 10초보다 짧게 제한
    - 빈 응답 방지: title/body 중 하나라도 비어 있으면 503 반환
    - LLM 실패 degrade: 모든 예외를 503으로 반환
    """
    service_ids = ",".join(g.service_id for g in req.services)
    try:
        result: NotificationTemplateResponse = await asyncio.wait_for(
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

    if not result.title.strip() or not result.body.strip():
        logger.warning(
            "알림 템플릿 LLM이 빈 응답 반환. service_ids=%s title=%r body=%r",
            service_ids,
            result.title,
            result.body,
        )
        raise HTTPException(status_code=503, detail=_503_DETAIL)

    return result
