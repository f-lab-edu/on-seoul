"""SSE 이벤트 emit 헬퍼 (자유 함수).

emit 헬퍼(`emit_answering`/`emit_router_events`)는 `self` 미사용 순수 state 함수다.
페이즈 분리 시 Intake/Planning/Retrieval 가 공유하던 교차 결합을 제거한다.
(intake 의 decision emit 은 agents/nodes/intake.py 의 `_emit_intake` 가 담당한다.)

저수준 writer 는 `agents/_helpers.py` 의 `emit_progress`/`emit_decision` 를 재사용한다.
동작·SSE emit 시점·횟수(decision 1회, progress 단계별 1회)는 원본과 동일하다.
"""

from typing import Any

from agents._helpers import emit_decision, emit_progress
from schemas.state import ActionType, AgentState, IntentType

# router 가 확정한 intent 중 검색이 진행되는 intent (searching progress 대상).
_SEARCHING_INTENTS = frozenset(
    {
        IntentType.SQL_SEARCH,
        IntentType.VECTOR_SEARCH,
        IntentType.MAP,
        IntentType.ANALYTICS,
    }
)


def emit_answering(state: AgentState) -> dict[str, Any]:
    """answering progress 를 1회 emit 하고 가드 슬롯 업데이트를 반환한다.

    이미 emit 됐으면(answering_emitted=True) no-op·빈 dict.
    반환은 {emit: {...}} 머지 부분 기록.
    """
    if state["emit"].get("answering_emitted"):
        return {}
    emit_progress("answering")
    return {"emit": {"answering_emitted": True}}


def emit_router_events(state: AgentState, update: dict[str, Any]) -> dict[str, Any]:
    """router_node 의 RETRIEVE emit — decision(routes) + searching/answering.

    triage 가 state 에 둔 user_rationale 을 읽어 decision 을 조립한다(보류 변수 불필요).
    decision 은 전체 실행 1회(재시도 재진입에도 유지), progress 는 단계별 1회
    (retry_prep_node 가 가드를 리셋해 재검색 시 다시 흐름).
    반환 dict 는 router_node 가 자기 update 에 머지해 가드 슬롯을 전파한다.
    """
    emit: dict[str, Any] = {}
    plan: dict[str, Any] = update.get("plan", {})
    rationale = state["triage"].get("user_rationale")
    # RETRIEVE decision: triage 의 rationale + router 가 확정한 routes.
    if rationale and not state["emit"].get("decision_emitted"):
        routes: list[str] = []
        primary = plan.get("intent")
        secondary = plan.get("secondary_intent")
        if primary is not None:
            routes.append(primary.value)
        if secondary is not None:
            routes.append(secondary.value)
        action = state["triage"].get("action")
        emit_decision(
            action.value if action else ActionType.RETRIEVE.value,
            routes,
            rationale,
        )
        emit["decision_emitted"] = True

    intent = plan.get("intent")
    if intent in _SEARCHING_INTENTS:
        if not state["emit"].get("searching_emitted"):
            emit_progress("searching")
            emit["searching_emitted"] = True
    else:
        # FALLBACK/error 등 — 검색 없이 answering.
        emit.update(emit_answering(state).get("emit", {}))
    return {"emit": emit} if emit else {}
