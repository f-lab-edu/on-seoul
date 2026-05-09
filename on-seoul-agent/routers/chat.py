"""POST /chat/stream — 챗봇 SSE 스트리밍 엔드포인트.

흐름:
    ChatRequest 수신
    → AgentState 구성 (title_needed = message_id == 1)
    → AgentWorkflow.run()
    → SSE StreamingResponse 반환

SSE 이벤트 3종:
    event: final          — 워크플로우 정상 완료
    event: workflow_error — 워크플로우 내부 에러 (fallback 답변 포함)
    event: error          — 세션/DB 레벨 예외

현재 Phase에서는 워크플로우 완료 후 단일 이벤트를 발행한다.
Phase 15(LangGraph 전환) 시 토큰 스트리밍으로 확장한다.
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agents.workflow import AgentWorkflow
from core.database import ai_session_ctx, data_session_ctx
from schemas.chat import ChatRequest
from schemas.state import AgentState

logger = logging.getLogger(__name__)

router = APIRouter()

# Lazy initialization — import 시점이 아닌 첫 요청 시 초기화한다.
# import 시점에 환경변수(GOOGLE_API_KEY 등)가 없으면 ConfigurationException이 발생하므로,
# AgentWorkflow() 생성을 첫 요청까지 미룬다.
# 테스트에서는 patch("routers.chat._workflow")로 None이 아닌 mock을 주입하면
# _get_workflow()가 mock을 반환하므로 기존 패치 방식이 그대로 동작한다.
_workflow: AgentWorkflow | None = None


def _get_workflow() -> AgentWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = AgentWorkflow()
    return _workflow

# SSE 응답 헤더 — 프록시/CDN 버퍼링 방지
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse_frame(event: str, data: dict) -> bytes:
    """SSE 프레임 직렬화.

    포맷:
        id: <uuid4>
        event: <event>
        data: <json>
        (빈 줄)
    """
    body = json.dumps(data, ensure_ascii=False)
    return f"id: {uuid.uuid4()}\nevent: {event}\ndata: {body}\n\n".encode()


async def _stream(request: ChatRequest) -> AsyncGenerator[bytes, None]:
    """워크플로우를 실행하고 SSE 프레임을 yield한다."""
    state = AgentState(
        room_id=request.room_id,
        message_id=request.message_id,
        message=request.message,
        title_needed=(request.message_id == 1),
        intent=None,
        lat=request.lat,
        lng=request.lng,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )

    try:
        async with data_session_ctx() as data_session, ai_session_ctx() as ai_session:
            result = await _get_workflow().run(
                state,
                data_session=data_session,
                ai_session=ai_session,
            )

        intent = result.get("intent")
        payload = {
            "message_id": result["message_id"],
            "answer": result.get("answer") or "",
            "intent": intent.value if intent is not None else None,
            "title": result.get("title"),
        }
        if result.get("error"):
            payload["error"] = result["error"]
            # 워크플로우가 완료됐지만 내부 오류로 fallback 답변을 반환한 경우
            yield sse_frame("workflow_error", payload)
        else:
            yield sse_frame("final", payload)

    except Exception:
        # 세션·DB 레벨 예외 — 워크플로우 진입 자체가 실패한 경우
        logger.exception("워크플로우 실행 중 오류")
        yield sse_frame("error", {"message": "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."})


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """사용자 메시지를 받아 에이전트 워크플로우를 실행하고 SSE로 응답한다."""
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
