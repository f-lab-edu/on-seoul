"""알림 템플릿 생성 라우터.

POST /notification/template — LLM으로 푸시 알림 title/body를 생성한다.
LLM 실패(타임아웃, 예외, 빈 응답) 시 503을 반환한다.
"""

import asyncio
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.config import settings
from llm.client import get_chat_model
from llm.prompts.notification import NOTIFICATION_FEW_SHOT_EXAMPLES, NOTIFICATION_SYSTEM
from schemas.notification import (
    NotificationTemplateRequest,
    NotificationTemplateResponse,
    ServiceChange,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notification", tags=["notification"])

_TIMEOUT_SECONDS = 8.0


def _verify_token(x_internal_token: str | None = Header(default=None)) -> None:
    """X-Internal-Token 헤더 검증 — admin.py와 동일한 내부 API 보호 패턴."""
    expected = settings.admin_internal_token
    if not expected:
        logger.warning("notification 엔드포인트 호출 거부 — admin_internal_token 미설정")
        raise HTTPException(status_code=401, detail="auth disabled")
    if x_internal_token is None or not secrets.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
_503_DETAIL = "알림 템플릿 생성에 실패했습니다."


def _format_changes(changes: list[ServiceChange]) -> str:
    """ServiceChange 목록을 LLM 입력용 텍스트로 변환한다."""
    lines = ["changes:"]
    for c in changes:
        lines.append(f"- change_type: {c.change_type}")
        lines.append(f"  field_name: {c.field_name if c.field_name is not None else 'null'}")
        lines.append(f"  old_value: {c.old_value if c.old_value is not None else 'null'}")
        lines.append(f"  new_value: {c.new_value if c.new_value is not None else 'null'}")
    return "\n".join(lines)


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

    human_content = _format_changes(req.changes)
    messages.append(HumanMessage(content=human_content))
    return messages


async def _invoke_llm(req: NotificationTemplateRequest) -> NotificationTemplateResponse:
    """LLM을 호출하여 NotificationTemplateResponse를 반환한다."""
    llm = get_chat_model(temperature=0.2)
    chain = llm.with_structured_output(NotificationTemplateResponse)
    messages = _build_messages(req)
    result = await chain.ainvoke(messages)
    return result


@router.post("/template", dependencies=[Depends(_verify_token)])
async def create_template(req: NotificationTemplateRequest) -> NotificationTemplateResponse:
    """서비스 변경 정보를 받아 알림 title/body를 생성한다.

    - self-timeout 8초: asyncio.wait_for로 클라이언트 10초보다 짧게 제한
    - 빈 응답 방지: title/body 중 하나라도 비어 있으면 503 반환
    - LLM 실패 degrade: 모든 예외를 503으로 반환
    """
    try:
        result: NotificationTemplateResponse = await asyncio.wait_for(
            _invoke_llm(req),
            timeout=_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "알림 템플릿 LLM 타임아웃 (%.1fs 초과). service_id=%s",
            _TIMEOUT_SECONDS,
            req.service_id,
        )
        raise HTTPException(status_code=503, detail=_503_DETAIL)
    except Exception:
        logger.exception("알림 템플릿 LLM 호출 실패. service_id=%s", req.service_id)
        raise HTTPException(status_code=503, detail=_503_DETAIL)

    if not result.title.strip() or not result.body.strip():
        logger.warning(
            "알림 템플릿 LLM이 빈 응답 반환. service_id=%s title=%r body=%r",
            req.service_id,
            result.title,
            result.body,
        )
        raise HTTPException(status_code=503, detail=_503_DETAIL)

    return result
