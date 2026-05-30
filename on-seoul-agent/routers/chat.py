"""POST /chat/stream — 챗봇 SSE 스트리밍 엔드포인트.

흐름:
    ChatRequest 수신
    → recent_queries fetch (Redis, per-room)
    → AgentState 구성 (title_needed = message_id == 1, recent_queries 주입)
    → AgentGraph.stream() 단계별 실행 (LangGraph StateGraph)
    → SSE StreamingResponse 반환
    → 정상 종료 시 사용자 message를 recent_queries 큐에 push

SSE 이벤트:
    event: progress       — 워크플로우 진행 단계 안내 (routing / searching / answering)
    event: final          — 워크플로우 정상 완료 (cache_hit 플래그 포함)
    event: workflow_error — 워크플로우 내부 에러 (fallback 답변 포함)
    event: error          — 세션/DB 레벨 예외
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agents.graph import AgentGraph
from core.database import ai_session_ctx, data_session_ctx
from core.recent_queries import get_recent_queries, push_recent_query
from core.redis import get_redis
from schemas.chat import ChatRequest
from schemas.state import AgentState

logger = logging.getLogger(__name__)

router = APIRouter()


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


def _resolve_redis(request: Request) -> Any:
    """request에서 redis를 조회. 없으면 새로 생성 (테스트/엣지 케이스)."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis = get_redis()
    return redis


def _resolve_graph(request: Request) -> AgentGraph:
    """request.app.state에서 AgentGraph를 조회. 없으면 새로 생성 (lifespan 미실행 환경 전용).

    프로덕션에서는 lifespan이 항상 실행되므로 fallback 경로는 호출되어서는 안 된다.
    테스트나 예외 상황에서 호출되면 경고 로그를 남긴다.
    """
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        logger.warning(
            "app.state.graph 미설정 — fallback AgentGraph 생성. lifespan 실행 여부 확인 필요"
        )
        graph = AgentGraph(redis=_resolve_redis(request))
    return graph


async def _stream(
    request: ChatRequest, graph: AgentGraph, redis: Any
) -> AsyncGenerator[bytes, None]:
    """워크플로우를 실행하고 SSE 프레임을 yield한다."""
    logger.info(
        "chat.request room=%s msg_id=%d msg=%r",
        request.room_id,
        request.message_id,
        request.message[:60],
    )
    # 1) 최근 질의 컨텍스트 — Router Agent follow-up 분류용. 장애 시 빈 리스트.
    recent_queries = await get_recent_queries(request.room_id, redis)

    state = AgentState(
        room_id=request.room_id,
        message_id=request.message_id,
        message=request.message,
        title_needed=(request.message_id == 1),
        intent=None,
        user_lat=request.lat,
        user_lng=request.lng,
        refined_query=None,
        max_class_name=None,
        area_name=None,
        service_status=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
        retry_count=0,
        recent_queries=recent_queries,
        cache_hit=False,
    )

    push_after_success = False
    try:
        async with data_session_ctx() as data_session, ai_session_ctx() as ai_session:
            async for event_type, data in graph.stream(
                state,
                data_session=data_session,
                ai_session=ai_session,
            ):
                if event_type == "progress":
                    yield sse_frame("progress", data)

                elif event_type == "result":
                    result = data
                    intent = result.get("intent")
                    payload = {
                        "message_id": result["message_id"],
                        "answer": result.get("answer") or "",
                        "intent": intent.value if intent is not None else None,
                        "title": result.get("title"),
                        "cache_hit": bool(result.get("cache_hit")),
                    }
                    if result.get("error"):
                        logger.error(
                            "chat.workflow_error room=%s intent=%s error=%s",
                            result.get("room_id"),
                            intent,
                            result["error"],
                        )
                        payload["error"] = "서비스 처리 중 오류가 발생했습니다."
                        yield sse_frame("workflow_error", payload)
                    else:
                        logger.info(
                            "chat.final room=%s intent=%s cache_hit=%s answer_len=%d",
                            result.get("room_id"),
                            intent.value if intent is not None else None,
                            payload["cache_hit"],
                            len(payload["answer"]),
                        )
                        push_after_success = True
                        yield sse_frame("final", payload)

    except Exception:
        # 세션·DB 레벨 예외 — 워크플로우 진입 자체가 실패한 경우
        logger.exception("워크플로우 실행 중 오류")
        yield sse_frame(
            "error",
            {"message": "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."},
        )
        return

    # 정상 final만 recent_queries에 push (workflow_error/error 경로는 push 안 함)
    if push_after_success:
        await push_recent_query(request.room_id, request.message, redis)


@router.post("/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """사용자 메시지를 받아 에이전트 워크플로우를 실행하고 SSE로 응답한다."""
    redis = _resolve_redis(http_request)
    graph = _resolve_graph(http_request)
    return StreamingResponse(
        _stream(request, graph, redis),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
