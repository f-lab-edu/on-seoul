"""POST /chat/stream — 챗봇 SSE 스트리밍 엔드포인트.

흐름:
    ChatRequest 수신
    → AgentState 구성 (title_needed = request.title_needed, history 주입)
    → AgentGraph.stream() 단계별 실행 (LangGraph StateGraph)
    → SSE StreamingResponse 반환

SSE 이벤트:
    event: progress       — 워크플로우 진행 단계 안내 (routing / searching / answering)
    event: title          — 첫 턴 대화 제목 (generate_title_node 독립 emit, payload type:"title")
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
from opentelemetry import context as otel_context
from opentelemetry import trace

from agents.graph import AgentGraph
from core.exceptions import RateLimitException
from core.redis import get_redis
from schemas.chat import ChatRequest
from schemas.state import AgentState, IntentType

logger = logging.getLogger(__name__)

# 모듈 tracer — OTel 비활성 시 no-op tracer 를 반환한다.
_tracer = trace.get_tracer(__name__)

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
    # default=str — service_cards 가 datetime 등 비-JSON-기본 타입을 포함할 수 있으므로
    # ISO 8601 문자열로 폴백 직렬화한다. answer_agent.py / nodes.py 의 다른 직렬화 지점과
    # 동일한 컨벤션을 적용해 SSE 스트림 중단(TypeError)을 방지한다.
    body = json.dumps(data, ensure_ascii=False, default=str)
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


def _build_prev_working_set(request: ChatRequest) -> dict[str, Any] | None:
    """ChatRequest → prev_working_set 중첩 채널.

    신규 채널(request.prev_working_set) 우선. 미전송 시 평면 슬롯(prev_entities/
    prev_intent/prev_reasoning)으로 폴백한다(하위호환). 폴백 시 신규 필드
    (refined_query/applied_filters/relaxed/relaxed_filters)는 None/기본값이다.

    셋 다 비어 있으면(첫 턴/구 클라이언트) None 을 반환해 현행 동작과 100% 동일하게 한다.
    """
    ws = request.prev_working_set
    if ws is not None:
        return {
            "entities": [e.model_dump() for e in ws.entities],
            "intent": ws.intent,
            "reasoning": ws.reasoning,
            "refined_query": ws.refined_query,
            "applied_filters": dict(ws.applied_filters),
            "relaxed": ws.relaxed,
            "relaxed_filters": list(ws.relaxed_filters),
        }
    # 평면 폴백 — 신규 필드는 없음.
    if not (request.prev_entities or request.prev_intent or request.prev_reasoning):
        return None
    return {
        "entities": [e.model_dump() for e in request.prev_entities],
        "intent": request.prev_intent,
        "reasoning": request.prev_reasoning,
        "refined_query": None,
        "applied_filters": {},
        "relaxed": False,
        "relaxed_filters": [],
    }


_WS_FILTER_KEYS = ("max_class_name", "area_name", "service_status", "payment_type")

# 실제 검색 레시피를 산출하는 intent — FALLBACK 은 대화형 분기 신호일 뿐 제외한다(버그 D).
_SEARCH_INTENTS = frozenset(
    {
        IntentType.SQL_SEARCH,
        IntentType.VECTOR_SEARCH,
        IntentType.MAP,
        IntentType.ANALYTICS,
    }
)


def _emit_working_set(result: dict[str, Any]) -> dict[str, Any]:
    """result(최종 AgentState) → 다음 턴 carryover 용 prev_working_set.

    applied_filters 는 result["filters"] (dict_merge 채널)에서 읽는다 — retry_prep 의
    완화 드롭이 머지로 반영된 *effective(완화 후)* 필터다(요청 필터가 아님).
    entities 는 노출된 service_cards 의 (service_id, label) 정체성만 담는다(레시피·정체성
    운반, 결과 스냅샷 아님).

    버그 D carry-forward: 이번 턴이 *새 검색 레시피를 만들지 않은* 비검색/무결과
    턴(META/EXPLAIN, 결과 없는 DIRECT_ANSWER/AMBIGUOUS/domain_outside)이면, 빈 워킹셋을
    내보내 직전 레시피를 덮지 않고 들어온 prev_working_set 을 그대로 carry-forward 한다.
    Spring opaque 패스스루는 last-message 를 저장·회신하므로, 비검색 턴이 직전 레시피를
    다시 emit 해야 후속(REFINE 등)이 올바른 base 를 받는다.

    판정 기준(silent 추정 금지): 이번 턴이 *새 레시피·결과를 산출했는가* —
    plan.intent 가 *실제 검색 의도*(SQL_SEARCH/VECTOR_SEARCH/MAP/ANALYTICS)이거나
    output.service_cards(새 노출 카드)가 있으면 "검색·결과 턴"으로 보고 result 기반
    생성(현행). 둘 다 없으면 "레시피 미생성 턴"으로 보고 carry-forward. 이 신호는 노드
    산출에서 직접 관측되며(explain/domain_outside 은 output.answer 만, RETRIEVE/REFINE 은
    검색 intent+cards, DRILL/RELEVANCE 은 cards), turn_kind 열거에 의존하지 않아 미래
    turn_kind 추가에도 안전하다.

    FALLBACK 보정(버그 D): direct_answer_node(answer.py:48)는 dict_merge 채널에
    plan.intent=FALLBACK 을 기록한다(EXPLAIN→direct_answer 폴백, answer.py:174 동일).
    FALLBACK 은 실제 검색 의도가 아니라 대화형 답변 분기 신호일 뿐이므로 *레시피로 치지
    않는다*. intent is not None 만으로 판정하면 결과 없는 DIRECT_ANSWER/EXPLAIN폴백 턴이
    '레시피 생성'으로 오인돼 직전 워킹셋을 빈 값으로 덮는다(버그 D 증상). _SEARCH_INTENTS
    멤버십으로 한정해 이를 차단한다.

    하위호환: prev_working_set 이 없으면(첫 턴/구 클라이언트) carry 대상이 없으므로
    기존(빈/현행) 동작을 그대로 반환한다.
    """
    plan = result.get("plan") or {}
    filters = result.get("filters") or {}
    output = result.get("output") or {}
    intent = plan.get("intent")
    cards = output.get("service_cards") or []

    # 레시피 미생성 턴(비검색·무결과) + 들어온 직전 워킹셋 있음 → carry-forward.
    prev = result.get("prev_working_set")
    # FALLBACK(대화형 분기) 은 레시피로 치지 않는다 — 실제 검색 intent 일 때만 인정.
    # IntentType 은 str Enum 이라 enum/문자열 양쪽 모두 frozenset 멤버십이 성립한다.
    produced_recipe = intent in _SEARCH_INTENTS or bool(cards)
    if not produced_recipe and prev:
        # entities 도 함께 carry — 비검색 턴은 새 카드가 없으므로 직전 entities 유지
        # (빈 배열로 덮지 않음). intent 는 그래프에 들어올 때 IntentType 일 수 있어
        # SSE 직렬화(default=str) 일관성을 위해 .value 로 정규화한다.
        prev_intent = prev.get("intent")
        prev_intent_str = (
            prev_intent.value if hasattr(prev_intent, "value") else prev_intent
        )
        return {
            "entities": list(prev.get("entities") or []),
            "intent": prev_intent_str,
            "reasoning": prev.get("reasoning"),
            "refined_query": prev.get("refined_query"),
            "applied_filters": dict(prev.get("applied_filters") or {}),
            "relaxed": bool(prev.get("relaxed")),
            "relaxed_filters": list(prev.get("relaxed_filters") or []),
        }

    entities = [
        {
            "service_id": str(c.get("service_id")),
            "label": str(c.get("service_name") or c.get("label") or ""),
        }
        for c in cards
        if c.get("service_id")
    ][:10]
    applied = {k: filters.get(k) for k in _WS_FILTER_KEYS if filters.get(k) is not None}
    return {
        "entities": entities,
        "intent": intent.value if intent is not None else None,
        "reasoning": (result.get("triage") or {}).get("user_rationale"),
        "refined_query": plan.get("refined_query"),
        "applied_filters": applied,
        "relaxed": bool(result.get("retry_relaxed")),
        "relaxed_filters": result.get("relaxed_filters") or [],
    }


async def _stream(
    request: ChatRequest,
    graph: AgentGraph,
    parent_ctx: otel_context.Context | None = None,
) -> AsyncGenerator[bytes, None]:
    """워크플로우를 실행하고 SSE 프레임을 yield한다.

    OTel 트레이스 연결: StreamingResponse 의 제너레이터 바디는 서버 span 활성
    시점(핸들러)이 아니라 ASGI 응답 스트리밍 단계에서 실행되므로, 핸들러가
    캡처해 넘긴 parent_ctx 를 여기서 attach 해 서버 span 컨텍스트를 재부착한다.
    이렇게 해야 graph.stream() 내부 httpx/SQLAlchemy span 이 /chat/stream 서버
    span 하위(같은 trace)로 연결된다. parent_ctx=None 이거나 OTel 비활성 시
    attach/detach/start_as_current_span 은 모두 no-op 이라 동작은 불변이다.
    """
    logger.info(
        "chat.request room=%s msg_id=%d msg=%r",
        request.room_id,
        request.message_id,
        request.message[:60],
    )
    prev_working_set = _build_prev_working_set(request)
    # 평면 슬롯은 prev_working_set 에서 파생한다(신규 채널 우선·평면 폴백 일원화).
    # intake/rehydrate/explain 등 평면 슬롯을 읽는 기존 경로의 하위호환을 보존한다.
    ws_entities = (prev_working_set or {}).get("entities") or []
    ws_intent = (prev_working_set or {}).get("intent")
    ws_reasoning = (prev_working_set or {}).get("reasoning")
    state = AgentState(
        # ── 보편 입력 (평면) ──
        room_id=request.room_id,
        message_id=request.message_id,
        message=request.message,
        title_needed=request.title_needed,
        user_lat=request.lat,
        user_lng=request.lng,
        history=[h.model_dump() for h in request.history],
        # ── carryover 입력 (평면) ──
        # 결과 엔티티 carryover + 참조 해소. 미전송 시 빈 배열/None →
        # intake_node 가 NEW(비참조)로 처리(기존 흐름 보존).
        prev_entities=ws_entities,
        prev_intent=ws_intent,
        prev_reasoning=ws_reasoning,
        prev_working_set=prev_working_set,
        target_service_ids=None,
        # ── 결과 품질 자각 패스 — pre_answer_gate_node 가 채운다 ──
        result_quality=None,
        reservation_guide_shown=False,
        # ── 운영-상세 발췌 — pre_answer_gate_node 가 operational_detail turn 에 채운다 ──
        detail_excerpt=None,
        # ── 재시도 제어 (평면) ──
        retry_count=0,
        retry_relaxed=False,
        relaxed_filters=None,
        forced_intent=None,
        retry_radius_m=None,
        # ── 오류/캐시 (평면) ──
        error=None,
        cache_hit=False,
        answer_lock_key=None,
        # ── 인프라/관측 (평면) ──
        node_path=[],
        search_channels={},
        trace=None,
        started_at=None,
        rrf_merged_ids=None,
        # ── 도메인 working state (중첩, 모두 {} 초기화) ──
        triage={},
        plan={},
        filters={},
        sql={},
        vector={},
        map={},
        analytics={},
        hydration={},
        output={},
        emit={},
    )

    # 핸들러에서 캡처한 서버 span 컨텍스트를 제너레이터 실행 컨텍스트에 재부착한다.
    # parent_ctx=None 이거나 OTel 비활성이면 attach 는 no-op token 을 반환한다.
    token = otel_context.attach(parent_ctx) if parent_ctx is not None else None
    try:
        # 제안 0-6: DB 노드가 세션을 노드 내부에서 acquire-use-release 하므로 여기서
        # 세션 스코프를 잡지 않는다. answer LLM 스트리밍 동안 커넥션을 점유하지 않는다.
        with _tracer.start_as_current_span("chat_stream.workflow") as _span:
            # SigNoz 트레이스 필터링용 식별자(PII 아님). 비활성 시 NonRecordingSpan no-op.
            _span.set_attribute("chat.room_id", request.room_id)
            _span.set_attribute("chat.message_id", request.message_id)
            async for event_type, data in graph.stream(state):
                if event_type == "progress":
                    yield sse_frame("progress", data)

                elif event_type == "decision":
                    yield sse_frame("decision", data)

                elif event_type == "title":
                    yield sse_frame("title", data)

                elif event_type == "sources_update":
                    yield sse_frame("sources_update", data)

                elif event_type == "result":
                    result = data
                    plan = result.get("plan") or {}
                    output = result.get("output") or {}
                    intent = plan.get("intent")
                    payload = {
                        "message_id": result["message_id"],
                        "answer": output.get("answer") or "",
                        "intent": intent.value if intent is not None else None,
                        "cache_hit": bool(result.get("cache_hit")),
                        "service_cards": output.get("service_cards") or [],
                        # 다음 턴 carryover 용 워킹셋(effective 필터 포함).
                        "prev_working_set": _emit_working_set(result),
                    }
                    if result.get("error"):
                        logger.error(
                            "chat.workflow_error room=%s intent=%s error=%s",
                            result.get("room_id"),
                            intent,
                            result["error"],
                        )
                        payload["error"] = "서비스 처리 중 오류가 발생했습니다."
                        # 에러 시 부분 결과 카드는 노출하지 않는다 —
                        # 에러 메시지 + 정상 카드 조합으로 인한 UI 혼란 방지.
                        payload["service_cards"] = []
                        yield sse_frame("workflow_error", payload)
                    else:
                        logger.info(
                            "chat.final room=%s intent=%s cache_hit=%s answer_len=%d",
                            result.get("room_id"),
                            intent.value if intent is not None else None,
                            payload["cache_hit"],
                            len(payload["answer"]),
                        )
                        yield sse_frame("final", payload)

    except RateLimitException:
        logger.warning(
            "chat.rate_limit room=%s — 임베딩 rate limit 소진", request.room_id
        )
        yield sse_frame(
            "error",
            {"message": "현재 요청이 많아 잠시 후 다시 시도해 주세요."},
        )
        return
    except Exception:
        # 세션·DB 레벨 예외 — 워크플로우 진입 자체가 실패한 경우
        logger.exception("워크플로우 실행 중 오류")
        yield sse_frame(
            "error",
            {"message": "서비스 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."},
        )
        return
    finally:
        # attach 한 컨텍스트는 항상 해제한다(정상/예외/조기 return 무관).
        if token is not None:
            otel_context.detach(token)


@router.post("/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    """사용자 메시지를 받아 에이전트 워크플로우를 실행하고 SSE로 응답한다."""
    graph = _resolve_graph(http_request)
    # 핸들러(FastAPIInstrumentor 서버 span 활성 시점)에서 현재 OTel 컨텍스트를
    # 캡처해 제너레이터로 전파한다. 제너레이터 바디는 별도 실행 컨텍스트라 여기서
    # 캡처하지 않으면 내부 span 이 부모 없이 별도 trace 로 떨어진다.
    parent_ctx = otel_context.get_current()
    return StreamingResponse(
        _stream(request, graph, parent_ctx),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
