"""입구 페이즈 — intake_node(분류·참조) + working_set_refine_node + route_intake.

reference_resolution(규칙) + triage(LLM)를 단일 LLM intake_node 로 병합한다(병합 근거:
LLM 이 service_id 를 생성하지 않고 인덱스만 반환하게 해 ID 환각을 차단). LLM 은
prev_entities 의 1-based 인덱스만 선택하고(_intake_indexing 순수 함수가 service_id 로
매핑), 같은 호출에서 turn_kind/action 을 판정한다. SQL/DB 는 손대지 않는다.
"""

import logging
from typing import Any

from agents import _emit
from agents._intake_indexing import resolve_ref_indices
from agents.intake_agent import IntakeAgent
from agents.nodes._shared import _FALLBACK_ANSWER, sanitize_user_rationale
from agents.router_agent import RouterAgent
from schemas.intake import IntakeAction, IntakeOutput, TurnKind
from schemas.state import ActionType, AgentState, IntentType

logger = logging.getLogger(__name__)

# intake action(NEW 위임) → 그래프 ActionType 매핑(EXPLAIN 제외 4종).
_ACTION_MAP: dict[IntakeAction, ActionType] = {
    IntakeAction.RETRIEVE: ActionType.RETRIEVE,
    IntakeAction.DIRECT_ANSWER: ActionType.DIRECT_ANSWER,
    IntakeAction.AMBIGUOUS: ActionType.AMBIGUOUS,
    IntakeAction.OUT_OF_SCOPE: ActionType.OUT_OF_SCOPE,
}

# 검색 머지 필터 키(working_set_refine 머지 대상).
_FILTER_KEYS = (
    "max_class_name",
    "area_name",
    "service_status",
    "payment_type",
    "target_audience",
)

# no_base 폴백 — 직전 검색 발화 후보에서 제외할 "필터 추가/잡담" 신호.
# 이런 토큰만으로 이뤄진 빈약한 후속은 토픽 base 가 아니라 델타이므로 건너뛴다.
_FOLLOWUP_REFINE_HINTS: tuple[str, ...] = (
    "다시",
    "그 중",
    "그중",
    "그것",
    "거기",
    "접수중",
    "무료",
    "유료",
    "바꿔",
    "말고",
)


def _last_search_user_turn(history: list[dict[str, str]]) -> str | None:
    """history 에서 직전 *검색성* user 발화를 찾는다(no_base 토픽 base 후보).

    과거→최신 순 history 를 역순 순회해, 빈약한 필터 추가/잡담 후속이 아닌 마지막
    user 발화를 반환한다(LLM 재분류 없이 안전 폴백). 못 찾으면 None.

    휴리스틱(보수적 — 잘못 고르면 환각 검색이므로 의심스러우면 건너뛴다):
      - 최소 길이(6자) 미만 발화 제외.
      - _FOLLOWUP_REFINE_HINTS(다시/그 중/접수중/무료 등) 를 포함하는 발화는 필터
        추가/잡담 후속으로 보고, 발화가 충분히 길지 않으면(< 18자) 토픽 후보에서
        제외한다. 짧은 발화에 후속-힌트가 끼면 거의 항상 델타이지 토픽이 아니다.
    """
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        content = (turn.get("content") or "").strip()
        if len(content) < 6:
            continue
        has_hint = any(h in content for h in _FOLLOWUP_REFINE_HINTS)
        if has_hint and len(content) < 18:
            continue
        return content
    return None


class IntakeNodes:
    """입구 페이즈 — intake_node + working_set_refine_node + route_intake.

    의존:
      - intake(IntakeAgent — 단일 LLM 분류). DB 미접촉.
      - router(RouterAgent — REFINE 경로에서 이번 발화의 신규 제약만 정제). DB 미접촉.
    """

    def __init__(
        self, intake: IntakeAgent | None, router: RouterAgent | None = None
    ) -> None:
        self._intake = intake or IntakeAgent()
        self._router = router or RouterAgent()

    async def intake_node(self, state: AgentState) -> dict[str, Any]:
        """입구 단일화 노드 — turn_kind + action + ref_indices 한 번에 판정.

        폴백 두 층위:
          (A) 분류 모호(파싱 실패/미지·누락) → turn_kind 기본 NEW + action 기본 RETRIEVE.
              기존 0건 게이트·self-correction 이 강등하므로 새 폴백 경로를 만들지 않는다.
              조작 ID 바인딩 금지. node_path 에 breadcrumb(silent no-op 금지).
          (B) 노드/LLM 예외 → DIRECT_ANSWER + error(기존 triage 예외 정책 계승) +
              self-correction 건너뜀. turn_kind 기본 NEW.
        """
        prev_entities = state.get("prev_entities") or []
        try:
            result = await self._intake.classify(
                state["message"],
                history=state.get("history") or [],
                prev_entities=prev_entities,
                prev_reasoning=state.get("prev_reasoning"),
            )
        except Exception as exc:
            # (B) 노드/LLM 예외 — 검색이 무의미하므로 안내 + self-correction 건너뜀.
            logger.exception("intake_node 실행 오류")
            update: dict[str, Any] = {
                "error": str(exc),
                "output": {"answer": _FALLBACK_ANSWER},
                "triage": {"action": ActionType.DIRECT_ANSWER, "turn_kind": "NEW"},
                "node_path": ["intake_error"],
            }
            # 정상 비-RETRIEVE 경로(_emit_intake)와 SSE progress 시퀀스를 대칭으로
            # 맞춘다 — 예외 폴백도 곧장 answering 단계로 진입하므로 가드를 흘린다.
            update.update(_emit.emit_answering(state))
            return update

        return self._build_update(state, result, prev_entities)

    def _build_update(
        self,
        state: AgentState,
        result: IntakeOutput,
        prev_entities: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """IntakeOutput → intake_node update (triage 채널 + 참조 바인딩 + decision)."""
        turn_kind = result.turn_kind
        breadcrumbs: list[str] = []

        # (A) 분류 모호 안전판: turn_kind 미지/누락 → NEW + RETRIEVE 강등(+ breadcrumb).
        if not isinstance(turn_kind, TurnKind):
            logger.warning(
                "intake_route_fallback room=%s turn_kind=%r → NEW",
                state.get("room_id"),
                turn_kind,
            )
            turn_kind = TurnKind.NEW
            breadcrumbs.append("intake_route_fallback")

        # NEW 일 때만 action 위임. 그 외 turn_kind 는 route_intake 가 분기를 결정하므로
        # action 은 RETRIEVE(검색 스킵 경로는 self_correction_edge ⓪이 비-RETRIEVE 만
        # 보므로, 검색 스킵 경로의 action 은 별도 의미 없음 — turn_kind 가 1차 스위치).
        action = ActionType.RETRIEVE
        oos_type = None
        if turn_kind == TurnKind.NEW:
            mapped = _ACTION_MAP.get(result.action)
            if mapped is None:
                logger.warning(
                    "intake_action_fallback room=%s action=%r → RETRIEVE",
                    state.get("room_id"),
                    result.action,
                )
                breadcrumbs.append("intake_action_fallback")
                mapped = ActionType.RETRIEVE
            action = mapped
            if action == ActionType.OUT_OF_SCOPE:
                oos_type = result.oos_type

        rationale = sanitize_user_rationale(result.user_rationale)

        # 참조 바인딩 — DRILL/RELEVANCE 만 인덱스 사용. 범위검증 순수 함수(ID 환각 0).
        target_ids: list[str] = []
        if turn_kind in (TurnKind.DRILL, TurnKind.RELEVANCE):
            target_ids = resolve_ref_indices(result.ref_indices, prev_entities)
            if not target_ids:
                # 참조인데 바인딩 실패(빈 prev_entities/범위 밖) → NEW + RETRIEVE 폴백.
                # 조작 ID 바인딩 금지(환각 차단) — 검색 경로로 정직하게 수렴.
                logger.warning(
                    "intake_route_fallback room=%s turn_kind=%s ref_indices=%s 바인딩 실패 → NEW",
                    state.get("room_id"),
                    turn_kind.value,
                    result.ref_indices,
                )
                breadcrumbs.append("intake_route_fallback")
                turn_kind = TurnKind.NEW
                action = ActionType.RETRIEVE

        logger.info(
            "intake.classify room=%s turn_kind=%s action=%s oos=%s targets=%s",
            state.get("room_id"),
            turn_kind.value,
            action.value,
            oos_type,
            target_ids,
        )

        update: dict[str, Any] = {
            "triage": {
                "action": action,
                "out_of_scope_type": oos_type,
                "user_rationale": rationale,
                "turn_kind": turn_kind.value,
            },
            "target_service_ids": target_ids or None,
            "node_path": ["intake", *breadcrumbs],
        }
        update.update(
            _emit_intake(state, turn_kind, action, rationale, target_ids, prev_entities)
        )
        return update

    async def working_set_refine_node(self, state: AgentState) -> dict[str, Any]:
        """REFINE 경로의 "주방" — 직전 워킹셋 + 이번 발화 신규 제약 머지 → 재검색.

        carryover 철학(스냅샷 아님): 직전 레시피(applied_filters)에 *이번 발화의 신규
        제약*을 더해 재검색한다. 신규 제약을 추출하지 않으면("그 중 무료만"의
        payment_type=무료) 직전 베이스로만 돌아 사용자 의도가 소실된다(MUST-FIX).

        택(a): RouterAgent 로 이번 message 를 정제해 신규 필터/refined_query 만 추출하고
        base(prev applied_filters)와 머지한다(신규가 우선). intent 는 prev 로 고정
        (forced_intent) — router_node 의 forced 분기가 intent 만 쓰고 filters/plan 은
        dict_merge 로 보존하므로, 다른 forced 경로(self-correction retry)에는 영향 없다.
        RouterAgent 는 SQL/DB 미접촉(필터 추출만) — carryover 원칙 유지.

        토픽 보존(C)·환각 차단(B)·폴백(Spring 비의존) 3겹:
          B. _parse_new_constraints 는 history 없이 router 를 호출한다. 빈약한 후속
             ("접수중인곳으로 다시 찾아줘")을 history 와 통째로 재분류시키면 발화에 없는
             필터(체육시설/마포구)를 지어낸다(history bleed). history 를 빼면 이번 발화에
             근거 있는 델타(접수중/무료)만 남는다.
          C. ws.refined_query 를 토픽 base 로 읽어 보존한다. 순수 필터 추가 후속이면
             직전 refined_query 를 유지하고(이번 발화가 새 토픽을 도입하지 않음), base
             가 없는 새 토픽 후속일 때만 이번 정제값으로 갱신한다.
          폴백. prev_working_set 이 비어 토픽 base 가 없을 때(no_base) 빈약한 후속을 그대로
             던지지 않고 history 의 직전 *검색성* user 발화를 토픽 base 로 삼아 재정제한다.
        """
        ws = state.get("prev_working_set") or {}
        applied = ws.get("applied_filters") or {}
        prev_intent = ws.get("intent")
        prev_refined = ws.get("refined_query")

        filters_base = {
            k: applied[k] for k in _FILTER_KEYS if applied.get(k) is not None
        }
        forced = prev_intent if isinstance(prev_intent, IntentType) else None

        # no_base 판정: applied_filters 와 refined_query 가 모두 없을 때만 carryover 할
        # 직전 레시피가 없는 것이다(C — refined_query 만 운반하는 VECTOR refine 오판 방지).
        no_base = not filters_base and not prev_refined

        if no_base:
            # 폴백 — Spring 이 prev_working_set 을 회신 안 한 경우. history 의 직전 검색성
            # 발화를 토픽 base 로 삼아 재정제(빈약한 후속을 그대로 검색하지 않는다).
            new_filters, refined_query = await self._parse_with_history_base(state)
        else:
            # 이번 발화의 신규 제약만 추출(history 미전달로 bleed 차단 — B).
            new_filters, this_refined = await self._parse_new_constraints(
                state["message"]
            )
            # C — 직전 토픽 보존: base 가 있으면 순수 필터 추가 후속으로 보고 prev_refined
            # 를 유지한다. prev_refined 가 없을 때만 이번 정제값으로 채운다.
            refined_query = prev_refined or this_refined

        # 머지: base 위에 신규를 얹어 신규가 우선(동일 키 충돌 시 이번 발화가 이긴다).
        # B 로 new_filters 에서 환각 필터가 제거됐으므로 이 규칙은 안전하다(base 토픽/
        # 카테고리를 hijack 하지 않는다).
        merged_filters = {**filters_base, **new_filters}

        logger.info(
            "working_set_refine room=%s base=%s new=%s merged=%s "
            "prev_refined=%r refined=%r no_base=%s forced_intent=%s",
            state.get("room_id"),
            filters_base,
            new_filters,
            merged_filters,
            prev_refined,
            refined_query,
            no_base,
            forced.value if forced else None,
        )
        # 관측 breadcrumb — REFINE 인데 carryover 베이스가 없으면 no_base 를 trace 에 남긴다.
        breadcrumbs = ["working_set_refine"]
        if no_base:
            breadcrumbs.append("working_set_refine:no_base")
        # re_searching 경계는 아니지만 검색 단계 진입이므로 searching emit 은 router_node
        # 가 RETRIEVE decision 과 함께 흘린다(여기선 가드만 세팅하지 않음).
        update: dict[str, Any] = {
            "node_path": breadcrumbs,
        }
        if merged_filters:
            update["filters"] = merged_filters
        if refined_query is not None:
            # plan 은 dict_merge 채널 — router_node forced 분기(intent 만 set)가 보존한다.
            update["plan"] = {"refined_query": refined_query}
        if forced is not None:
            update["forced_intent"] = forced
        return update

    async def _parse_new_constraints(
        self, message: str
    ) -> tuple[dict[str, Any], str | None]:
        """이번 발화에서 신규 post-filter + refined_query 만 추출한다(intent 는 버림).

        history 를 *전달하지 않는다*(B) — 빈약한 후속을 history 와 통째로 재분류시키면
        발화에 없는 필터를 지어내는 bleed 가 발생한다. 이 함수는 message 단독으로만
        정제해 발화에 근거 있는 델타만 남긴다.

        RouterAgent.classify 가 enum 정규화(area_name/payment_type 등)를 이미 수행하므로
        그 결과의 non-None 필터만 골라 dict 로 반환한다. LLM/네트워크 실패는 best-effort
        로 흡수해 base 필터만으로 재검색을 계속한다(REFINE 흐름을 막지 않음).
        """
        try:
            result = await self._router.classify(message)
        except Exception:
            logger.exception(
                "working_set_refine 신규 제약 정제 실패 — base 필터만 사용"
            )
            return {}, None
        new_filters = {
            k: getattr(result, k)
            for k in _FILTER_KEYS
            if getattr(result, k, None) is not None
        }
        return new_filters, result.refined_query

    async def _parse_with_history_base(
        self, state: AgentState
    ) -> tuple[dict[str, Any], str | None]:
        """no_base 폴백 — history 직전 검색 발화를 토픽 base 로 삼아 델타만 더한다.

        prev_working_set 이 비어 carryover 베이스가 없을 때, 빈약한 후속을 그대로
        검색하지 않는다. history 의 직전 *검색성* user 발화(META/잡담 아닌 마지막
        질의)를 토픽 base 로 두고, 이번 발화의 신규 제약을 그 위에 얹어 재정제한다.

        직전 검색 발화를 못 찾으면 이번 발화 단독으로만 정제한다(발화에 없는 필터를
        만들지 않음 — 환각 차단). 토픽 base 가 있으면 "<직전 검색> <이번 발화>" 를
        합쳐 router 에 던져 토픽을 잇는다.
        """
        base_utterance = _last_search_user_turn(state.get("history") or [])
        if base_utterance is None:
            # 직전 검색 발화 부재 — 이번 발화 단독 정제(환각 필터 0).
            return await self._parse_new_constraints(state["message"])
        # 토픽 base + 이번 델타를 합쳐 정제(history 인자는 여전히 미전달 — bleed 차단).
        combined = f"{base_utterance} {state['message']}"
        return await self._parse_new_constraints(combined)

    # ------------------------------------------------------------------
    # 라우팅 — 단일 조건부 엣지 route_intake (turn_kind 1차 + NEW action 서브스위치)
    # ------------------------------------------------------------------

    def route_intake(self, state: AgentState) -> str:
        """intake_node 직후 — turn_kind 1차 분기 + NEW→action 서브스위치.

        REFINE → working_set_refine_node (머지 필터 재검색)
        DRILL/RELEVANCE → rehydrate_node → describe_node (검색 스킵)
        META → explain_node
        NEW → action 서브스위치(RETRIEVE→router / DIRECT_ANSWER→direct /
              AMBIGUOUS→ambiguous / OUT_OF_SCOPE→out_of_scope)
        (B) 노드 예외(error + answer) → answer_node 직행.
        """
        error = state.get("error")
        answer = state["output"].get("answer") or ""
        if error and answer.strip():
            return "answer_node"

        turn_kind = state["triage"].get("turn_kind")
        if turn_kind == TurnKind.REFINE.value:
            return "working_set_refine_node"
        if turn_kind in (TurnKind.DRILL.value, TurnKind.RELEVANCE.value):
            return "rehydrate_node"
        if turn_kind == TurnKind.META.value:
            return "explain_node"

        # NEW(또는 기본 폴백) → action 서브스위치.
        action = state["triage"].get("action")
        if action == ActionType.RETRIEVE:
            return "router_node"
        if action == ActionType.DIRECT_ANSWER:
            return "direct_answer_node"
        if action == ActionType.AMBIGUOUS:
            return "ambiguous_node"
        if action == ActionType.OUT_OF_SCOPE:
            return "out_of_scope_node"
        # should-never-happen 방어 — RETRIEVE 로 수렴(0건 게이트가 강등).
        logger.warning(
            "route_intake: 미처리 turn_kind=%r action=%r → router_node room=%s",
            turn_kind,
            action,
            state.get("room_id"),
        )
        return "router_node"


def _emit_intake(
    state: AgentState,
    turn_kind: TurnKind,
    action: ActionType,
    rationale: str | None,
    target_ids: list[str],
    prev_entities: list[dict[str, Any]],
) -> dict[str, Any]:
    """intake decision 이벤트 단일 발행 + answering/searching 가드.

    RETRIEVE(NEW) 는 router_node 가 routes 확정 후 decision 을 emit 하므로 여기선
    decision 을 미루고(triage 가 state 에 rationale 을 두면 router 가 읽음), 검색 스킵
    경로(REFINE/DRILL/RELEVANCE/META, 비-RETRIEVE NEW)는 여기서 decision 을 발행한다.
    """
    # RETRIEVE(NEW) + REFINE 은 검색을 돌리므로 decision 을 router_node 로 위임.
    if turn_kind == TurnKind.NEW and action == ActionType.RETRIEVE:
        return {}
    if turn_kind == TurnKind.REFINE:
        return {}

    emit: dict[str, Any] = {}
    if rationale and not state["emit"].get("decision_emitted"):
        # decision 라벨: 선택된 인덱스의 라벨(범위검증 통과분)을 함께 노출(soft 오선택 투명화).
        from agents._helpers import emit_decision

        labels = _selected_labels(target_ids, prev_entities)
        rationale_with_label = rationale
        if labels:
            rationale_with_label = f"{rationale} (선택: {', '.join(labels)})"
        emit_decision(
            action.value if action else ActionType.RETRIEVE.value,
            [],
            rationale_with_label,
        )
        emit["decision_emitted"] = True

    # 검색 스킵 경로는 곧장 answering 단계로.
    emit.update(_emit.emit_answering(state).get("emit", {}))
    return {"emit": emit} if emit else {}


def _selected_labels(
    target_ids: list[str], prev_entities: list[dict[str, Any]]
) -> list[str]:
    """바인딩된 service_id 의 라벨을 prev_entities 에서 조회(decision 노출용)."""
    by_id = {e.get("service_id"): (e.get("label") or "") for e in prev_entities}
    return [by_id[sid] for sid in target_ids if by_id.get(sid)]
