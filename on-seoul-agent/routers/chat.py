"""POST /chat/stream — 챗봇 SSE 스트리밍 엔드포인트.

흐름:
    ChatRequest 수신
    → AgentState 구성 (title_needed = message_id == 1)
    → AgentWorkflow.run()
    → SSE StreamingResponse 반환

SSE 이벤트 2종:
    event: final — 워크플로우 정상 완료
    event: error — 워크플로우 예외 발생

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

# 모듈 레벨 싱글톤 — 테스트에서 patch("routers.chat._workflow")로 교체 가능
_workflow = AgentWorkflow()

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
            result = await _workflow.run(
                state,
                data_session=data_session,
                ai_session=ai_session,
            )

        yield sse_frame(
            "final",
            {
                "message_id": result["message_id"],
                "answer": result["answer"],
                "intent": result["intent"].value if result["intent"] else None,
                "title": result.get("title"),
            },
        )

    except Exception as exc:
        logger.exception("chat/stream 처리 중 예외 발생")
        yield sse_frame("error", {"message": str(exc)})


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """사용자 메시지를 받아 에이전트 워크플로우를 실행하고 SSE로 응답한다."""
    return StreamingResponse(
        _stream(request),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
