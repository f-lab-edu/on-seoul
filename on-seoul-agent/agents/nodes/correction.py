"""자기 교정 페이즈 — retry_prep 노드 + self_correction 엣지·0건 판정 헬퍼."""

import logging
from typing import Any

from agents import _redis_gateway
from agents._helpers import emit_progress
from agents.nodes._shared import is_gap_oos
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

# attribute_gap 완화(M1) — 드롭 우선순위. 케이스 C/ANALYTICS 완화와 일관되게
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

    의존: redis(재진입 전 answer 락 해제).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def retry_prep_node(self, state: AgentState) -> dict[str, Any]:
        """자기 교정 재시도 준비 노드 (intent별 방향성 분기).

        _self_correction_edge에서 재시도가 결정될 때만 실행된다.
        retry_count를 1 증가시키고 intent에 따라 전환/완화/반경확장을 수행한다.

        분기:
          - 케이스 M1 (attribute_gap): OUT_OF_SCOPE/attribute_gap vector 0건 →
            forced_intent=VECTOR_SEARCH + refined_query/vector_sub_intent 보존,
            0건 유발 필터만 드롭(max_class_name 유지) + relaxed_filters 기록.
          - 케이스 A (전환): _RETRY_FALLBACK_INTENT 키 intent(SQL_SEARCH 등) →
            forced_intent 세팅 + 정형 필터 전부 비움(전환 경로가 자체 정제).
          - 케이스 B (ANALYTICS): 가장 제약 큰 effective 필터 1개만 드롭(status→area).
            max_class_name 은 유지. 드롭할 게 없으면 no-op.
          - 케이스 D (MAP): retry_radius_m=3000 으로 반경 확장, map_results 리셋.
          - 케이스 C (기존 완화): VECTOR_SEARCH 0건/빈 답변 등 — 필터·refined_query 리셋.

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

        # C2 0건 게이트 우회 경로 회귀 방지: 이 재시도는 cache_write 를 거치지 않고
        # router_node 로 재진입하므로, 직전 패스의 cache_check 가 잡은 singleflight 락을
        # 여기서 해제해야 한다(미해제 시 재진입 cache_check 가 SET NX 실패 → poll 타임아웃).
        # Option B: 획득 시점 키를 평면 슬롯에서 그대로 읽어 해제 — refined_query/필터
        # 리셋 이전·이후 순서와 무관하게 키 불일치 위험이 없다. release 는 멱등.
        lock_key = state.get("answer_lock_key")
        if lock_key:
            await _redis_gateway.release_answer_lock(lock_key, self._redis)

        # 재시도 경계: re_searching emit + progress 가드 리셋(다음 순회의
        # searching/answering 이벤트가 다시 흐르게 한다 — 기존 동작 보존).
        # decision_emitted 는 리셋하지 않는다(decision 은 전체 실행 1회 — emit 머지로 보존).
        emit_progress("re_searching")

        # 모든 분기 공통 베이스 — 분기별 override 로 검색 슬롯/필터를 덮어쓴다.
        # emit 은 머지 채널이라 부분 키만 보낸다(decision_emitted 보존).
        update: dict[str, Any] = {
            "retry_count": new_retry_count,
            "error": None,
            "retry_relaxed": True,
            "search_channels": RESET_CHANNELS,
            "node_path": ["retry_prep"],
            "emit": {"searching_emitted": False, "answering_emitted": False},
            # 해제 완료 → 슬롯 비움(재진입 cache_check 가 새 락 키를 다시 기록).
            "answer_lock_key": None,
        }
        # 전 분기 공통 필터 드롭 페이로드(머지) — 케이스 B(ANALYTICS)만 부분 드롭.
        _filters_clear = {
            "max_class_name": None,
            "area_name": None,
            "service_status": None,
            "payment_type": None,
        }
        # 큐레이션 의도 복원용 — 전체 드롭 직전 원래(비-None) 필터값 스냅샷.
        # filters 채널이 None 으로 비워진 뒤에도 pre_answer_gate 가 원 요청 제약을
        # 복원해 적합도 정렬할 수 있게 한다(케이스 A/C 완화 경로).
        _relaxed_snapshot = {
            f: state["filters"].get(f)
            for f in _filters_clear
            if state["filters"].get(f)
        }

        # 케이스 M1(attribute_gap): OUT_OF_SCOPE/attribute_gap 의 vector 검색 0건.
        # 케이스 C(전체 리셋)와 달리 검색 컨텍스트를 보존한다:
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

        # 케이스 A: 강제 전환 대상 intent (SQL_SEARCH → VECTOR_SEARCH 등)
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

        # 케이스 B: ANALYTICS — 가장 제약 큰 effective 필터 1개만 드롭(intent 유지)
        if intent == IntentType.ANALYTICS:
            update["analytics"] = {}
            for field in _ANALYTICS_DROP_ORDER:
                if state["filters"].get(field):
                    update["filters"] = {field: None}  # 한 개만 드롭(머지)하고 중단
                    break
            return update

        # 케이스 D: MAP — 반경 확장(intent 유지)
        # 케이스 C 와 달리 sql/vector/hydration 그룹을 건드리지 않는다: MAP 경로는
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

        # 케이스 C: 기존 완화 (VECTOR_SEARCH 0건, 빈 답변 등)
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
        """
        # ⓪ 비-RETRIEVE action은 self-correction 제외
        action = state["triage"].get("action")
        if action is not None and action != ActionType.RETRIEVE:
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
