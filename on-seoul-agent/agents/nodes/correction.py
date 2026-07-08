"""자기 교정 페이즈 — retry_prep 노드 + self_correction 엣지·0건 판정 헬퍼."""

import logging
from typing import Any

from agents._helpers import emit_progress
from agents.nodes._shared import is_gap_oos
from schemas.critic import ALLOWED_DROP_FILTERS
from schemas.search import RESET_CHANNELS
from schemas.state import ActionType, AgentState, IntentType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 방향성 self-correction 재시도 레지스트리 (retry_prep_node 분기 제어)
# ---------------------------------------------------------------------------

# 검색 실패 → 폴백 intent 강제 전환 레지스트리.
# 0건인 원 intent 가 키에 있으면 value 로 강제 전환한다. 확장은 한 줄.
_RETRY_FALLBACK_INTENT: dict[IntentType, IntentType] = {
    IntentType.SQL_SEARCH: IntentType.VECTOR_SEARCH,
    # IntentType.MAP: IntentType.VECTOR_SEARCH,  # 추후 확장
}

# ANALYTICS 완화 — 제약 강도 역순 드롭 우선순위. 한 번에 1개만 드롭.
# max_class_name 은 의미 보존상 유지(드롭 대상 제외).
# analytics_keyword 는 state 로 제어 불가능한 필드라 드롭 대상에서 제외한다:
# analytics_search 에 전달되는 keyword 는 state["analytics_keyword"](trace 관측 전용
# 출력 슬롯)가 아니라 AnalyticsAgent.run 이 매 실행 LLM 으로 message 에서 재추출하는
# params.keyword 다. 따라서 state 드롭은 무효(재실행 시 동일 keyword 재추출) → 0건
# 재현·무효 재시도 낭비. 실효성 있는 effective 필터(service_status/area_name)만 드롭한다.
_ANALYTICS_DROP_ORDER: tuple[str, ...] = (
    "service_status",
    "area_name",
)

# attribute_gap 완화 — 드롭 우선순위. 기존 완화/ANALYTICS 완화와 일관되게
# 제약 강도 순으로 드롭하되 max_class_name(카테고리)은 의미 보존상 유지(드롭 제외).
# 동일 vector 질의를 "필터만 완화"해 재검색하기 위해 0건을 유발한 필터를 모두 드롭한다.
_ATTRIBUTE_GAP_DROP_ORDER: tuple[str, ...] = (
    "payment_type",
    "service_status",
    "area_name",
)

# MAP 0건 완화 — 반경 확장(1회). 기본 1000m → 3000m.
_MAP_RETRY_RADIUS_M: int = 3000


class CorrectionNodes:
    """자기 교정 페이즈 — retry_prep 노드 + self_correction 엣지·0건 판정 헬퍼.

    의존: redis(생성자 주입 슬롯 — 현재 노드 로직에서 직접 사용하지 않는다. answer 락은
    전 요청 수명 동안 K_original 에 유지되고 cache_write 가 단독 해제하므로 retry_prep 는
    락을 건드리지 않는다. 주입 일관성·하위호환을 위해 슬롯은 보존한다).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def retry_prep_node(self, state: AgentState) -> dict[str, Any]:
        """자기 교정 재시도 준비 노드 (intent별 방향성 분기).

        _self_correction_edge에서 재시도가 결정될 때만 실행된다.
        retry_count를 1 증가시키고 intent에 따라 전환/완화/반경확장을 수행한다.

        분기:
          - attribute_gap: OUT_OF_SCOPE/attribute_gap vector 0건 →
            forced_intent=VECTOR_SEARCH + refined_query/vector_sub_intent 보존,
            0건 유발 필터만 드롭(max_class_name 유지) + relaxed_filters 기록.
          - 전환: _RETRY_FALLBACK_INTENT 키 intent(SQL_SEARCH 등) →
            forced_intent 세팅 + 정형 필터 전부 비움(전환 경로가 자체 정제).
          - ANALYTICS: 가장 제약 큰 effective 필터 1개만 드롭(status→area).
            max_class_name 은 유지. 드롭할 게 없으면 no-op.
          - MAP: retry_radius_m=3000 으로 반경 확장, map_results 리셋.
          - 기존 완화: VECTOR_SEARCH 0건/빈 답변 등 — 필터·refined_query 리셋.

        모든 분기는 공통 베이스(retry_count 증가 + error 클리어 + retry_relaxed=True +
        RESET_CHANNELS)를 공유하고 분기별 override 만 더한다. retry_count 캡(최대 1회)을
        동일하게 받으며 retry_relaxed=True 로 AnswerAgent 가 완화 사실을 답변에 명시한다.
        RESET_CHANNELS sentinel 로 이전 시도 채널 데이터를 지워
        UNIQUE (message_id, channel) 위반을 막는다(빈 dict({}) 는 no-op 이라 sentinel 필수).
        """
        new_retry_count = (state.get("retry_count") or 0) + 1
        intent = state["plan"].get("intent")
        action = state["triage"].get("action")
        oos_type = state["triage"].get("out_of_scope_type")
        logger.info(
            "retry.triggered room=%s retry_count=%d intent=%s action=%s",
            state.get("room_id"),
            new_retry_count,
            intent.value if intent else None,
            action.value if action else None,
        )

        # 락은 여기서 해제하지 않는다(회귀 방지 핵심): answer_lock_key(= 최초 cache_check
        # 시점 K_original)는 이제 락 대상이자 cache_write 의 저장 타깃을 겸한다. 재시도
        # 재진입 시 cache_check 는 이 슬롯이 있으면 즉시 pass-through 하므로(재획득 안 함)
        # 락을 미리 풀 필요가 없다 — 락은 최초 획득부터 cache_write 저장까지 K_original 에
        # 걸린 채 유지되고, 그 사이 동일 원 질의의 대기자는 계속 폴한다. 여기서 해제하면
        # K_original 락이 사라져 대기자가 고아가 되고(저장 전 fail-open), 재진입 cache_check
        # 가드로 재획득도 안 하므로 락 정합이 깨진다. 따라서 answer_lock_key 도 보존한다
        # (아래 update 에서 슬롯을 건드리지 않는다).

        # 재시도 경계: re_searching emit + progress 가드 리셋(다음 순회의
        # searching/answering 이벤트가 다시 흐르게 한다 — 기존 동작 보존).
        # decision_emitted 는 리셋하지 않는다(decision 은 전체 실행 1회 — emit 머지로 보존).
        emit_progress("re_searching")

        # 모든 분기 공통 베이스 — 분기별 override 로 검색 슬롯/필터를 덮어쓴다.
        # emit 은 머지 채널이라 부분 키만 보낸다(decision_emitted 보존).
        # critic 슬롯은 항상 클리어한다(1회성): critic 힌트를 이번 재시도에서 소비했든,
        # 결정적 폴백으로 왔든, 다음 순회로 이월되지 않아야 한다(L1 §3-3 폴백 층).
        update: dict[str, Any] = {
            "retry_count": new_retry_count,
            "error": None,
            "retry_relaxed": True,
            "search_channels": RESET_CHANNELS,
            "node_path": ["retry_prep"],
            "emit": {"searching_emitted": False, "answering_emitted": False},
            "critic_decision": None,
            "critic_replan_hint": None,
            "critic_rationale": None,
            # answer_lock_key 는 보존한다(슬롯 미기록 = LangGraph 머지에서 무변경).
            # 락은 전 요청 수명 동안 K_original 에 유지되고, cache_write 가 저장 후 단독
            # 해제한다. 재진입 cache_check 는 이 슬롯을 보고 즉시 pass-through(재획득 안 함).
        }

        # ── critic REPLAN 힌트 소비(L1 Phase 3, escalation 경로) ──
        # critic 이 방향을 정했으면(intent 전환/필터 드롭/질의 재구성) 그 힌트를 우선
        # 소비한다. 힌트가 없으면(critic 미발동/미결정/폴백) 아래 기존 결정적 규칙으로
        # 내려간다(§3-3 폴백 층). 인젝션 가드: 힌트는 스키마(IntentType enum +
        # 화이트리스트 필터명 Literal + 자연어 재구성)로만 표현 가능하므로 화이트리스트
        # 검증을 여기서 다시 하지 않아도 자유 식별자는 애초에 담길 수 없다. 실제
        # 파라미터화는 router 검증 경로가 수행한다.
        hint = state.get("critic_replan_hint")
        if hint:
            return self._apply_critic_hint(state, update, hint)
        # 전 분기 공통 필터 드롭 페이로드(머지) — ANALYTICS 분기만 부분 드롭.
        _filters_clear = {
            "max_class_name": None,
            "area_name": None,
            "service_status": None,
            "payment_type": None,
            "target_audience": None,
        }
        # 큐레이션 의도 복원용 — 전체 드롭 직전 원래(비-None) 필터값 스냅샷.
        # filters 채널이 None 으로 비워진 뒤에도 pre_answer_gate 가 원 요청 제약을
        # 복원해 적합도 정렬할 수 있게 한다(전환/기존 완화 경로).
        _relaxed_snapshot = {
            f: state["filters"].get(f)
            for f in _filters_clear
            if state["filters"].get(f)
        }

        # attribute_gap 분기: OUT_OF_SCOPE/attribute_gap 의 vector 검색 0건.
        # 기존 완화(전체 리셋)와 달리 검색 컨텍스트를 보존한다:
        #   · forced_intent=VECTOR_SEARCH 로 2회차 router_node 가 LLM 재분류를 skip 하게 한다.
        #   · vector_sub_intent(identification 등)·refined_query 는 보존(plan 리셋 금지) —
        #     "동일 vector 질의를 필터만 완화해 재검색"이 성립한다.
        #   · 0건을 유발한 필터만 드롭(max_class_name 유지)하고 relaxed_filters 에 기록한다.
        # 종료 안전성: 2회차는 answer_node 도달 후 self_correction_edge ⓪
        # (action==OUT_OF_SCOPE → end_normal) 로 즉시 종료되어 무한루프 없음.
        if action == ActionType.OUT_OF_SCOPE and is_gap_oos(oos_type):
            dropped = [
                f for f in _ATTRIBUTE_GAP_DROP_ORDER if state["filters"].get(f)
            ]
            update.update(
                {
                    "forced_intent": IntentType.VECTOR_SEARCH,  # 평면(1회성)
                    # 검색 결과/하이드 슬롯만 리셋 — plan(refined_query/sub_intent)은 보존.
                    "vector": {},
                    "hydration": {},
                    # 드롭 대상 필터만 None 으로(머지) — max_class_name 등 미드롭 항목은 유지.
                    "filters": {f: None for f in dropped},
                    "relaxed_filters": dropped,
                    # 큐레이션 의도 복원용 — 드롭 *직전* 원래 값을 보존한다(filters 는 None 됨).
                    "relaxed_values": {f: state["filters"].get(f) for f in dropped},
                }
            )
            return update

        # 전환 분기: 강제 전환 대상 intent (SQL_SEARCH → VECTOR_SEARCH 등)
        fallback = _RETRY_FALLBACK_INTENT.get(intent) if intent else None
        if fallback is not None:
            update.update(
                {
                    "forced_intent": fallback,  # 평면
                    # 결과/하이드 그룹 통째 리셋 (reducer 없음 → {} = 빈 상태).
                    "sql": {},
                    "vector": {},
                    "map": {},
                    "hydration": {},
                    # plan 머지: refined_query 만 비우고 intent/sub/secondary 는 보존.
                    "plan": {"refined_query": None},
                    # 전환 시 정형 필터는 유지하지 않는다(전환 경로가 자체 정제, 머지).
                    "filters": dict(_filters_clear),
                    "relaxed_filters": list(_relaxed_snapshot.keys()) or None,
                    "relaxed_values": _relaxed_snapshot or None,
                }
            )
            return update

        # ANALYTICS 분기 — 가장 제약 큰 effective 필터 1개만 드롭(intent 유지)
        if intent == IntentType.ANALYTICS:
            update["analytics"] = {}
            for field in _ANALYTICS_DROP_ORDER:
                if state["filters"].get(field):
                    update["filters"] = {field: None}  # 한 개만 드롭(머지)하고 중단
                    break
            return update

        # MAP 분기 — 반경 확장(intent 유지)
        # 기존 완화와 달리 sql/vector/hydration 그룹을 건드리지 않는다: MAP 경로는
        # 이 슬롯들을 채우지 않으므로 리셋 자체가 무의미하다(반경만 확장하면 충분).
        if intent == IntentType.MAP:
            update.update(
                {
                    "map": {},
                    # map_node 가 이 값을 기본 반경 대신 사용한다(평면).
                    "retry_radius_m": _MAP_RETRY_RADIUS_M,
                }
            )
            return update

        # 기존 완화 분기 (VECTOR_SEARCH 0건, 빈 답변 등)
        # payment_type 완화 — 0건 재시도 시 결제 유형 필터를 드롭한다.
        update.update(
            {
                "sql": {},
                "vector": {},
                "map": {},
                "hydration": {},
                "plan": {"refined_query": None},
                "filters": dict(_filters_clear),
                "relaxed_filters": list(_relaxed_snapshot.keys()) or None,
                "relaxed_values": _relaxed_snapshot or None,
            }
        )
        return update

    @staticmethod
    def _apply_critic_hint(
        state: AgentState,
        update: dict[str, Any],
        hint: dict[str, Any],
    ) -> dict[str, Any]:
        """critic REPLAN 힌트를 재시도 update 에 반영한다(escalation 경로, §3-3).

        힌트는 세 방향 힌트의 조합이다(모두 선택적):
          · intent          → forced_intent 전환(router 2회차 재분류 skip). 검색/하이드
                              슬롯 리셋(전환 경로가 자체 정제).
          · reformulate_query → plan.refined_query 재설정(벡터 재검색용 자연어).
          · drop_filters    → 화이트리스트 필터만 None 드롭 + relaxed 기록.

        어느 힌트도 실효가 없으면(빈 hint) 결정적 완화 규칙과 동일한 전체 리셋으로
        폴백한다(무의미한 동일 재검색 방지). breadcrumb: retry_prep:critic.
        """
        update["node_path"] = ["retry_prep:critic"]

        applied = False

        # intent 전환 — 화이트리스트 enum 만(스키마 보장). 파싱 실패는 무시(폴백).
        intent_raw = hint.get("intent")
        if intent_raw:
            try:
                update["forced_intent"] = IntentType(intent_raw)
            except ValueError:
                logger.warning("critic hint intent 무효(무시): %r", intent_raw)
            else:
                # 전환 시 검색/하이드 결과는 리셋(전환 경로가 재검색). plan 은 머지로
                # refined_query 만 아래에서 다룬다(sub_intent/secondary 보존).
                update["sql"] = {}
                update["vector"] = {}
                update["map"] = {}
                update["hydration"] = {}
                applied = True

        # 질의 재구성 — plan 머지(refined_query 만). SQL 아님(자연어).
        reformulate = hint.get("reformulate_query")
        if reformulate:
            plan_update = dict(update.get("plan") or {})
            plan_update["refined_query"] = reformulate
            update["plan"] = plan_update
            applied = True

        # 필터 드롭 — 화이트리스트 교차만(스키마 밖 값은 방어적으로 무시).
        drop_filters = hint.get("drop_filters") or []
        valid_drops = [
            f
            for f in drop_filters
            if f in ALLOWED_DROP_FILTERS and state["filters"].get(f)
        ]
        if valid_drops:
            update["filters"] = {f: None for f in valid_drops}
            update["relaxed_filters"] = valid_drops
            update["relaxed_values"] = {
                f: state["filters"].get(f) for f in valid_drops
            }
            applied = True

        if applied:
            return update

        # 실효 힌트 없음 — 결정적 완화(전체 리셋)로 폴백해 무의미한 동일 재검색을 막는다.
        update.update(
            {
                "sql": {},
                "vector": {},
                "map": {},
                "hydration": {},
                "plan": {"refined_query": None},
                "filters": {
                    "max_class_name": None,
                    "area_name": None,
                    "service_status": None,
                    "payment_type": None,
                    "target_audience": None,
                },
            }
        )
        return update

    def self_correction_edge(self, state: AgentState) -> str:
        """answer_node 완료 후 자기 교정 여부를 결정한다.

        평가 순서(고정) — 다중 조건 동시 참 시 비결정성을 제거한다. 위에서부터
        먼저 매칭되는 하나만 적용(1회 캡이므로 단일 완화):
          ⓪ 비-RETRIEVE action(DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN) → end_normal.
          ① retry_count 캡: 이미 1회 소진 → 종료(무한 루프 방지).
          ② 빈 답변: intent 무관 최우선 재시도(기존 동작).
          ③ intent별 0건:
             - SQL_SEARCH/VECTOR_SEARCH → _hard_filter_zero_hits
             - ANALYTICS               → _analytics_zero_hits
             - MAP                     → _map_zero_hits

        intent 분기는 상호배타라 한 순회에 하나만 평가된다. retry_prep_node 가
        retry_count 를 1 로 올리므로 다음 순회에서는 ①에서 즉시 종료된다.

        L1 critic 상호작용(§3-2, 예산 이중 카운트 금지): critic 이 이번 라운드에
        결정을 냈으면(critic_decision 세팅) 검색 결과 품질 재시도는 critic 이 이미
        소유한다(REPLAN 은 route_critic 이 retry_prep 로 보냈고, ANSWER/STOP 은 명시적
        "재시도 안 함"이다). 따라서 critic 결정이 있으면 self_correction 은 개입하지
        않는다(end_normal) — critic STOP 을 존중하고 0건 재시도 이중 트리거를 막는다.
        (critic 미진입/폴백이면 critic_decision 은 None 이라 기존 결정적 경로 그대로.)
        """
        # ⓪ 비-RETRIEVE action은 self-correction 제외
        action = state["triage"].get("action")
        if action is not None and action != ActionType.RETRIEVE:
            return "end_normal"

        # ⓪-bis critic 이 이번 라운드 결정을 소유했으면 self_correction 미개입.
        if state.get("critic_decision") is not None:
            return "end_normal"

        retry_count = state.get("retry_count", 0)
        if retry_count != 0:
            return "end_normal"  # ① 캡

        answer = state["output"].get("answer") or ""
        if not answer.strip():
            return "retry_prep_node"  # ② 빈 답변 (최우선, intent 무관)

        intent = state["plan"].get("intent")  # ③ intent별 0건
        if intent in (IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH):
            if self._hard_filter_zero_hits(state):
                return "retry_prep_node"
        elif intent == IntentType.ANALYTICS:
            if self._analytics_zero_hits(state):
                return "retry_prep_node"
        elif intent == IntentType.MAP:
            if self._map_zero_hits(state):
                return "retry_prep_node"

        return "end_normal"

    @staticmethod
    def _hard_filter_zero_hits(state: AgentState) -> bool:
        """검색·하이드레이션 슬롯이 모두 비어 있는지(0건) 판정한다."""
        return not (
            state["hydration"].get("hydrated_services")
            or state["sql"].get("results")
            or state["vector"].get("results")
        )

    @staticmethod
    def _analytics_zero_hits(state: AgentState) -> bool:
        """ANALYTICS 결과가 없거나(0행) error 인지 판정한다."""
        if state.get("error"):
            return True
        return not state["analytics"].get("results")  # [] / None 모두 True

    @staticmethod
    def _map_zero_hits(state: AgentState) -> bool:
        """MAP 반경 내 0건인지 판정한다.

        lat/lng 미제공(map.results=None)은 위치 안내가 최선이므로 재시도 제외.
        features=[] (반경 내 0건)만 반경 확장 재시도 대상이다.
        """
        mr = state["map"].get("results")
        if mr is None:
            return False
        return not (mr.get("features") or [])
