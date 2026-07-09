"""검색 페이즈 — sql/vector/map/analytics/hydration + rrf/게이트 노드·엣지."""

import logging
from typing import Any

from agents import _emit
from agents._helpers import (
    assess_result_quality,
    emit_critic_decision,
    reservation_guide_already_shown,
)
from agents._ondata_gateway import OnDataReader, default_reader
from agents.answer_agent import _DISPLAY_LIMIT, _curate_display, _normalize_card_row
from agents.detail_excerpt import extract_operational_keywords, prepare_detail_excerpt
from agents._search_channel_utils import _to_hits
from agents.analytics_agent import AnalyticsAgent
from agents.nodes._shared import (
    apply_structured_gate,
    is_gap_oos,
    sanitize_user_rationale,
)
from agents.hydration_node import HydrationNode
from agents.retrieval_critic import RetrievalCritic
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from core.config import settings
from core.exceptions import RateLimitException
from core.rrf import reciprocal_rank_fusion
from schemas.search import (
    ChannelData,
    ChannelQuery,
    SearchChannel,
    SearchKind,
)
from schemas.state import ActionType, AgentState, IntentType
from tools.map_search import DEFAULT_RADIUS_M as _MAP_DEFAULT_RADIUS_M
from tools.map_search import TOP_K as _MAP_TOP_K
from tools.sql_search import TOP_K as _SQL_TOP_K

logger = logging.getLogger(__name__)


class RetrievalNodes:
    """검색 페이즈 — sql/vector/map/analytics/hydration + rrf/게이트 노드·엣지.

    의존: sql(SqlAgent), vector(VectorAgent), analytics(AnalyticsAgent),
    hydration(HydrationNode), ondata(OnDataReader — on_data 읽기 게이트웨이).

    B3-1(선택적 주입): on_data 세션·tool 접근을 `OnDataReader` 생성자 주입으로 받는다.
    테스트는 가짜 OnDataReader 를 주입해 patch 없이 격리한다(설계 기준 ④ 최소 명시 주입).
    기본값은 프로세스 공유 default_reader 라 프로덕션 동작은 불변이다.
    """

    def __init__(
        self,
        sql: SqlAgent,
        vector: VectorAgent,
        analytics: AnalyticsAgent,
        hydration: HydrationNode,
        ondata: OnDataReader | None = None,
        critic: RetrievalCritic | None = None,
    ) -> None:
        self._sql = sql
        self._vector = vector
        self._analytics = analytics
        self._hydration = hydration
        self._ondata = ondata or default_reader
        # L1 retrieval-critic(escalation 게이트 승격 노드). None 이면 critic 경로가
        # 비활성이라 게이트가 결정적 폴백만 탄다(회귀 0). 주입은 GraphNodes 가 담당.
        self._critic = critic

    async def rrf_fusion_node(self, state: AgentState) -> dict[str, Any]:
        """SQL + VECTOR 병렬 팬아웃 결과를 RRF로 통합한다.

        secondary_intent 있고 enable_secondary_intent=True인 경우에만 실행된다.
        그 외에는 bypass(빈 dict 반환).

        SQL 결과(sql_results)와 vector 결과(vector_results)를 동일 레벨로 RRF 통합.
        통합된 결과는 hydrated_services로 직접 매핑되지 않고, hydration_node가
        rrf_merged_ids 슬롯을 읽어 처리한다.

        단순 구현: sql_results와 vector_results의 service_id를 각각 채널로 입력하여
        RRF 점수 기준으로 재정렬한 service_id 순서를 rrf_merged_ids에 적재한다.
        hydration_node가 이 슬롯을 우선 참조하여 hydrate_services를 호출한다.
        """
        if not settings.enable_secondary_intent:
            return {"node_path": ["rrf_fusion_bypass"]}

        secondary = state["plan"].get("secondary_intent")
        if secondary is None:
            return {"node_path": ["rrf_fusion_bypass"]}

        sql_rows = state["sql"].get("results") or []
        vector_rows = state["vector"].get("results") or []

        sql_ids = [r["service_id"] for r in sql_rows if r.get("service_id")]
        vector_ids = [r["service_id"] for r in vector_rows if r.get("service_id")]

        if not sql_ids and not vector_ids:
            logger.info("rrf_fusion: 두 채널 모두 0건 room=%s", state.get("room_id"))
            return {"node_path": ["rrf_fusion_empty"]}

        channels: dict[str, list[str]] = {}
        if sql_ids:
            channels["sql"] = sql_ids
        if vector_ids:
            channels["vector"] = vector_ids

        fused = reciprocal_rank_fusion(channels, k_constant=settings.rrf_k_constant)
        merged_ids = [sid for sid, _ in fused[: settings.rrf_top_k_final]]

        logger.info(
            "rrf_fusion.done room=%s sql=%d vector=%d merged=%d",
            state.get("room_id"),
            len(sql_ids),
            len(vector_ids),
            len(merged_ids),
        )
        return {"rrf_merged_ids": merged_ids, "node_path": ["rrf_fusion_node"]}

    async def pre_answer_gate_node(self, state: AgentState) -> dict[str, Any]:
        """pre-answer 0건 게이트 + 결과 품질 자각 패스.

        hydration_node 직후 hydrated_services=[] 이면 answer_node를 미호출하고
        retry_prep_node로 직행하도록 엣지 로직(route_pre_answer_gate)에서 판정한다.

        자각 패스: RETRIEVE(hydration 결과) 경로에서만 결과 성격(쏠림·빈약)을
        경량 휴리스틱으로 점검해 answer 가 소비할 평면 슬롯 result_quality 를 산출한다.
        재검색은 하지 않고(라우팅 불변, 전진 1회) answer 톤만 바꾼다. 통합회원 안내
        반복 억제용 reservation_guide_shown(history 상류 파싱, answer 는 bool 만 소비)도
        함께 적재한다. 점검 예외는 result_quality=None 으로 격리(best-effort).

        attribute_gap(OUT_OF_SCOPE)·describe·MAP·ANALYTICS 는 자각 패스 비대상이라
        result_quality 슬롯을 건드리지 않는다(None 유지).

        운영-상세 prep: operational_detail turn 이면 focal 단건 detail_content 를
        fetch + 발췌해 detail_excerpt 슬롯에 적재한다(없으면 None = attribute_gap interim
        폴백 신호). fetch·정제·발췌는 상류(여기/tools/agents)가 담당하고 answer 는 소비만
        한다(책임 경계). 예외는 best-effort 격리(답변 막지 않음).
        """
        result_quality: dict[str, Any] | None = None
        reservation_shown = False
        curated_display: list[dict[str, Any]] | None = None
        curated_extra_count: int | None = None
        curated_alt_count: int | None = None
        gated_hydration: dict[str, Any] | None = None
        if self._is_retrieve_path(state):
            try:
                rows = state["hydration"].get("hydrated_services") or []
                # post-RRF 구조화 게이트 — 채널 누출 차단. 벡터의 summary/question/
                # bm25 채널로 들어온 타 지역·타 카테고리("체육시설 말고" 여집합)·상충
                # 대상 행을 원본 area_name/max_class_name/target_info 로 최종 교정한다.
                # SQL 경로는 WHERE 로 이미 걸려 있어 이 게이트를 다시 통과해도
                # 불변이라 무해하다.
                filters = state.get("filters") or {}
                gated = apply_structured_gate(
                    rows,
                    area_names=filters.get("area_name"),
                    max_class_names=filters.get("max_class_name"),
                    target_audience=filters.get("target_audience"),
                )
                if len(gated) != len(rows):
                    # 게이트가 행을 제거했을 때만 hydration 슬롯을 재기록한다. 0건이
                    # 되면 route_pre_answer_gate 가 0건 게이트→critic/retry 로 완화
                    # 재검색을 태운다(무한루프 없음, retry 캡).
                    gated_hydration = {"hydrated_services": gated}
                    rows = gated
                # 카드 큐레이션 — 카드형 턴에서만, result_quality *이전*에 실행한다.
                # curated/display 산출 후 그 기준으로 품질을 점검해 정합을
                # 맞춘다. 비카드형/0건은 큐레이션 스킵(슬롯 None 유지).
                quality_rows = rows
                if self._is_card_turn(state) and rows:
                    normalized = [_normalize_card_row(r) for r in rows]
                    intended = self._intended_constraints(state)
                    curated, alt_count = _curate_display(
                        normalized,
                        intended,
                        relaxed=bool(state.get("retry_relaxed")),
                        relaxed_filters=state.get("relaxed_filters"),
                    )
                    curated_display = curated[:_DISPLAY_LIMIT]
                    curated_extra_count = max(0, len(curated) - _DISPLAY_LIMIT)
                    curated_alt_count = alt_count
                    # result_quality 는 큐레이션된 display 기준으로 산출(정합).
                    quality_rows = curated_display
                result_quality = assess_result_quality(
                    quality_rows, area_filter=state["filters"].get("area_name")
                )
                reservation_shown = reservation_guide_already_shown(
                    state.get("history")
                )
            except Exception:
                # best-effort: 점검 실패가 답변을 막지 않는다(현행 조립으로 폴백).
                logger.exception("pre_answer_gate 결과 품질/큐레이션 점검 실패")
                result_quality = None
                curated_display = None
                curated_extra_count = None
                curated_alt_count = None

        detail_excerpt = await self._prepare_operational_detail(state)
        update: dict[str, Any] = {
            "result_quality": result_quality,
            "reservation_guide_shown": reservation_shown,
            "curated_display": curated_display,
            "curated_extra_count": curated_extra_count,
            "curated_alt_count": curated_alt_count,
            "detail_excerpt": detail_excerpt,
            "node_path": ["pre_answer_gate"],
        }
        # 게이트가 행을 제거했으면 hydration 슬롯을 교정 결과로 덮어쓴다(후속 엣지·
        # answer 가 동일 축소셋을 본다). 미제거면 슬롯을 건드리지 않는다(무변경).
        if gated_hydration is not None:
            update["hydration"] = gated_hydration
        return update

    @staticmethod
    def _is_card_turn(state: AgentState) -> bool:
        """카드 목록형 턴인지 판정한다(SQL/VECTOR 비-identification).

        큐레이션 대상은 answer 의 카드형(Tier 2) 경로와 정확히 일치해야 한다 —
        identification(상세형)/attribute_gap/operational_detail/MAP/ANALYTICS/FALLBACK 은
        목록 나열형이 아니라 제외한다. 분기 신호(intent/vector_sub_intent)는 answer 가
        쓰는 것과 동일하다(중복 정의 아님, 상류 평가).
        """
        intent = state["plan"].get("intent")
        if intent not in (IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH):
            return False
        sub_intent = state["plan"].get("vector_sub_intent")
        # identification(상세)·attribute_gap·operational_detail 은 카드 목록형 아님.
        if sub_intent in ("identification", "attribute_gap", "operational_detail"):
            return False
        return True

    @staticmethod
    def _intended_constraints(state: AgentState) -> dict[str, Any]:
        """큐레이션 적합도 정렬용 의도 제약을 복원한다(5.1).

        effective(완화 후) filters 채널의 비-None 값에, 완화로 드롭된 원래 값
        (relaxed_values)을 합쳐 *원 요청* 제약을 복원한다. 완화 후 applied 가 비어도
        relaxed_values 로 area_name/max_class_name/payment_type 를 되살린다.
        """
        intended: dict[str, str | None] = {}
        filters = state.get("filters") or {}
        for key in ("area_name", "max_class_name", "payment_type"):
            value = filters.get(key)
            if value:
                intended[key] = value
        for key, value in (state.get("relaxed_values") or {}).items():
            if value and key not in intended:
                intended[key] = value
        return intended

    async def _prepare_operational_detail(self, state: AgentState) -> str | None:
        """operational_detail turn 의 focal detail_content fetch + 발췌(best-effort).

        operational_detail 이 아니거나 focal 부재면 즉시 None(블롭 격리: 다른 경로는
        detail_content 를 절대 fetch 하지 않는다). 키워드 부재/길이<게이트/예외 → None
        (= attribute_gap interim 폴백 신호). raw 블롭은 발췌까지 끝낸 문자열로만 좁혀
        반환하므로 카드/answer 에 원문이 실리지 않는다.
        """
        if state["plan"].get("vector_sub_intent") != "operational_detail":
            return None
        rows = state["hydration"].get("hydrated_services") or []
        if not rows:
            return None
        focal_id = rows[0].get("service_id")
        if not focal_id:
            return None
        try:
            raw = await self._ondata.fetch_detail_content(focal_id)
            keywords = extract_operational_keywords(state.get("message") or "")
            return prepare_detail_excerpt(raw, keywords)
        except Exception:
            logger.exception("operational_detail 발췌 prep 실패 room=%s", state.get("room_id"))
            return None

    @staticmethod
    def _is_retrieve_path(state: AgentState) -> bool:
        """결과 품질 자각 패스 평가 대상 — 순수 RETRIEVE(hydration) 경로인지 판정한다.

        attribute_gap(OUT_OF_SCOPE)·describe·MAP·ANALYTICS 는 비대상이다.
        action=None 은 router fallback(검색 실행)이라 RETRIEVE 와 동일 취급한다.
        """
        return state["triage"].get("action") in (ActionType.RETRIEVE, None)

    def route_pre_answer_gate(self, state: AgentState) -> str:
        """pre-answer 게이트 엣지 — escalation-gated critic 진입 결정 (L1 Phase 3).

        결정 순서(위에서부터, 먼저 매칭되는 하나):
          ⓪ 비검색 경로 → answer_node(직접 답변, 기존 동작 불변).
          ① 예산 소진(retry_count >= max_retrieval_retries) → answer_node(하드 백스톱).
          ② critic 활성 + critic 주입됨:
             · 의심스러움(0건/thin/skew) → retrieval_critic_node(LLM 1회 승격).
             · 명백히 좋음 → answer_node(critic 미호출 = 80% 빠른 경로 보존).
          ③ critic 비활성/미주입(폴백) → 기존 결정적 경로:
             0건(retry_count==0) → retry_prep_node, 그 외 → answer_node.

        gap(attribute_gap/operational_detail, OUT_OF_SCOPE)은 vector 검색을 실제
        실행하므로 RETRIEVE 와 동일한 검색 경로로 취급한다. 그 외 비-RETRIEVE action 은
        게이트 통과(직접 answer).
        """
        action = state["triage"].get("action")
        oos_type = state["triage"].get("out_of_scope_type")
        # action=None 은 route_intake 의 else→router_node fallback(검색 실행)과
        # 대칭이라 RETRIEVE 와 동일 취급한다. 입구에서 검색을 돌려놓고 이 게이트만
        # 건너뛰면 빈 컨텍스트로 answer_node 에 진입하므로 None 도 검색 경로로 본다.
        # gap(attribute_gap/operational_detail)은 검색 경로라 의심 체크에 포함한다.
        is_search_path = action in (ActionType.RETRIEVE, None) or (
            action == ActionType.OUT_OF_SCOPE and is_gap_oos(oos_type)
        )
        if not is_search_path:
            return "answer_node"  # ⓪

        retry_count = state.get("retry_count", 0)
        # ① 예산 소진 하드 백스톱 — critic·결정적 경로 공히 재시도 불가(무한 루프 차단).
        # self_correction 과 단일 예산(max_retrieval_retries)을 공유해 이중 카운트 없음.
        if retry_count >= settings.max_retrieval_retries:
            return "answer_node"

        # ② escalation 게이트 — critic 활성 + 주입 시에만 승격 판정.
        # critic 미주입/비활성이면 ③ 결정적 폴백으로 내려간다(fail-open).
        if settings.enable_retrieval_critic and self._critic is not None:
            if self._is_suspicious(state):
                return "retrieval_critic_node"
            return "answer_node"  # 명백히 좋음 — critic 미호출(80% 경로).

        # ③ 결정적 폴백(기존 동작 불변): 0건 → retry_prep, 유건 → answer.
        # []=검색 실행·0건(→retry) vs None=hydration 미실행(→통과)을 구분한다.
        # 기존 1회 캡 보존을 위해 retry_count==0 을 유지한다(플래그 오프 회귀 0).
        hydrated = state["hydration"].get("hydrated_services")
        if hydrated is not None and len(hydrated) == 0 and retry_count == 0:
            return "retry_prep_node"
        return "answer_node"

    @staticmethod
    def _is_suspicious(state: AgentState) -> bool:
        """검색 결과가 "의심스러운지"(critic 승격 대상) 결정적으로 판정한다.

        신호(전부 *결과* 기반, 계획서 §3-1 A):
          · 0건(hydrated_services 가 명시적 빈 리스트 — 검색 실행 후 0건).
          · thin(result_quality.thin — pre_answer_gate 가 산출한 빈약 신호).
          · skew(result_quality.skew_field 존재 — 한 값 쏠림 신호).
        어느 것도 아니면 "명백히 좋음"으로 보아 critic 을 부르지 않는다(80% 경로).
        """
        return RetrievalNodes._entry_signal(state) is not None

    @staticmethod
    def _entry_signal(state: AgentState) -> str | None:
        """critic escalation 을 유발한 결과 신호를 분류한다(관측·SSE 라벨용, L1 Phase 5).

        우선순위(먼저 매칭 하나): "zero"(0건) → "thin"(빈약) → "skew"(쏠림).
        의심스럽지 않으면(명백히 좋음) None — critic 미진입. 집계 신호 라벨만
        산출하며 raw 텍스트/식별정보는 담지 않는다(PII 차단).
        """
        hydrated = state["hydration"].get("hydrated_services")
        if hydrated is not None and len(hydrated) == 0:
            return "zero"
        quality = state.get("result_quality") or {}
        if quality.get("thin"):
            return "thin"
        if quality.get("skew_field"):
            return "skew"
        return None

    def route_critic(self, state: AgentState) -> str:
        """retrieval_critic_node 직후 엣지 — critic 3택 소비 + fail-open 폴백.

          · ANSWER → answer_node(결과로 답한다).
          · REPLAN → retry_prep_node(방향 힌트 소비 후 재검색). 단 예산 소진이면 answer.
          · STOP   → answer_node(정직한 한계 안내).
          · None(critic 미결정, fail-open §3-1 F) → 기존 결정적 경로:
              0건 → retry_prep, 유건 → answer.
        """
        decision = state.get("critic_decision")
        retry_count = state.get("retry_count", 0)
        # critic 루프는 단일 예산 N(max_retrieval_retries=2)을 쓴다(계획서 결정 D). 이는
        # 레거시 결정적 경로(route_pre_answer_gate 폴백 branch·self_correction_edge 의
        # retry_count==0 1회 캡)보다 관대하나, 게이트 하드 백스톱(retry_count>=N)이 모든
        # 재진입에서 잘라내 max N 으로 bounded 하다(무한루프 불가).
        budget_left = retry_count < settings.max_retrieval_retries

        if decision == "ANSWER":
            return "answer_node"
        if decision == "STOP":
            return "answer_node"
        if decision == "REPLAN":
            # 예산 소진 시 REPLAN 은 실행 불가 — answer 직행(하드 백스톱).
            return "retry_prep_node" if budget_left else "answer_node"

        # decision is None — critic 실패/미결정 fail-open. 기존 결정적 규칙으로.
        hydrated = state["hydration"].get("hydrated_services")
        if hydrated is not None and len(hydrated) == 0 and budget_left:
            return "retry_prep_node"
        return "answer_node"

    async def retrieval_critic_node(self, state: AgentState) -> dict[str, Any]:
        """escalation 게이트가 승격한 검색 비평 노드 — RetrievalCritic.critique 위임.

        critic 이 검색 결과 요약을 보고 critic 3슬롯(decision/replan_hint/rationale)을
        채운다. LLM 예외/미결정은 critic 내부에서 fail-open 처리되어(세 슬롯 None) 상위
        route_critic 이 결정적 폴백으로 라우팅한다 — 이 노드가 예외로 그래프를 깨지 않는다.

        방어: critic 미주입인데도 이 노드에 도달하면(정상 경로에선 게이트가 차단) fail-open
        breadcrumb 만 남기고 세 슬롯을 None 으로 둔다.

        관측·SSE(L1 Phase 5, best-effort): critic 이 결정을 낸 뒤 그 근거를
        `critic_decision` SSE 이벤트로 사용자에게 투명하게 노출하고(sanitize_user_rationale
        로 내부 식별자 제거), Langfuse 에 라운드 스팬(진입 신호·decision·round)을 남긴다.
        emit/span 실패는 그래프 결과를 막지 않는다(Core rule 8).
        """
        if self._critic is None:
            return {
                "critic_decision": None,
                "critic_replan_hint": None,
                "critic_rationale": None,
                "node_path": ["retrieval_critic:no_critic"],
            }
        # escalation 을 유발한 신호(관측·span 라벨). critique 전에 결과 상태로 결정한다.
        entry_signal = self._entry_signal(state) or "unknown"
        update = await self._critic.critique(state)
        self._observe_critic(state, update, entry_signal)
        return update

    @staticmethod
    def _observe_critic(
        state: AgentState,
        update: dict[str, Any],
        entry_signal: str,
    ) -> None:
        """critic 결정의 SSE 노출 + Langfuse 스팬을 best-effort 로 기록한다 (L1 Phase 5).

        · SSE: critic_rationale 을 sanitize 해 critic_decision 이벤트로 emit(라운드마다
          1회 — round=retry_count 로 라운드를 구분해 중복 키 충돌 없음). rationale 이 없거나
          sanitize 후 비면 emit 을 건너뛴다(triage decision 의 rationale=None 가드와 동일).
        · Langfuse: 라운드 스팬(진입 신호·decision·round) 집계 메타데이터만 기록.
        관측 실패가 그래프/답변을 막지 않는다(Core rule 8): SSE emit 은 자체 try 로
        격리하고, span 기록은 emit 실패와 독립적으로 수행한다(record_critic_span 이
        내부적으로 client no-op·예외 삼킴하는 best-effort). 지연 import·state 읽기는
        타입이 보증돼 예외가 나지 않는다.
        """
        # 지연 import: agents.graph 는 이 모듈(agents.nodes.retrieval)을 간접 import 하므로
        # 모듈 상단 import 시 순환이 된다 — 노드 실행 시점에만 끌어온다.
        from agents.graph import record_critic_span

        round_index = state.get("retry_count", 0)
        decision = update.get("critic_decision")
        try:
            rationale = sanitize_user_rationale(update.get("critic_rationale"))
            if decision is not None and rationale:
                emit_critic_decision(decision, round_index, rationale)
        except Exception:
            logger.warning("critic_decision SSE emit 실패(무시)", exc_info=True)
        record_critic_span(entry_signal, decision, round_index)

    async def sql_node(self, state: AgentState) -> dict[str, Any]:
        """SqlAgent.search() 호출 — sql_results + search_channels 설정.

        노드 로컬 세션(0-6): data_session 을 풀에서 잡고 쿼리 후 즉시 반납한다.

        answering progress 는 여기서 emit 하지 않는다. sql_node 는 secondary_intent
        팬아웃(enable_secondary_intent=True)으로 vector_node 와 동일 super-step 에 병렬
        실행될 수 있어, 두 노드가 각자 emit 하면 answering 이 2회 흐른다(회귀). emit 은
        팬아웃·단일 라우트·attribute_gap 경로가 모두 합류하는 단일 머지점 hydration_node
        가 1회 담당한다(graph.py: sql_node/vector_node → hydration_node).
        """
        try:
            async with self._ondata.session() as data_session:
                new_state = await self._sql.search(state, data_session)
            sql_slot = new_state.get("sql") or {}
            sql_rows = sql_slot.get("results") or []
            keyword = sql_slot.get("keyword")
            logger.info(
                "sql.results room=%s count=%d", state.get("room_id"), len(sql_rows)
            )

            filters = state["filters"]
            channel_data = ChannelData(
                kind=SearchKind.SQL,
                query=ChannelQuery(
                    query_text=keyword,
                    parameters={
                        "max_class_name": filters.get("max_class_name"),
                        "area_name": filters.get("area_name"),
                        "service_status": filters.get("service_status"),
                        "payment_type": filters.get("payment_type"),
                        "keyword": keyword,
                        "top_k": _SQL_TOP_K,
                    },
                ),
                hits=_to_hits(sql_rows, score_field=None),
            )
            return {
                "sql": {"results": sql_slot.get("results"), "keyword": keyword},
                "search_channels": {SearchChannel.SQL: channel_data},
                "node_path": ["sql_node"],
            }
        except Exception as exc:
            logger.exception("sql_node 실행 오류")
            return {"error": str(exc), "node_path": ["sql_error"]}

    async def vector_node(self, state: AgentState) -> dict[str, Any]:
        """VectorAgent.search() 호출 — vector_results(메타데이터 only), refined_query 설정.

        hydration(원본 조회)은 후속 hydration_node 가 담당한다.
        세션 관리(제안 2): VectorAgent.search() 내부에서 4채널마다 독립 ai_session_ctx() 로
        세션을 열고 asyncio.gather 병렬 실행한다. vector_node 는 세션을 직접 다루지 않는다.

        answering progress 는 여기서 emit 하지 않는다. vector_node 는 secondary_intent
        팬아웃(enable_secondary_intent=True)으로 sql_node 와 동일 super-step 에 병렬
        실행될 수 있어, 두 노드가 각자 emit 하면 answering 이 2회 흐른다(회귀). emit 은
        합류 머지점 hydration_node 가 1회 담당한다(graph.py: vector_node → hydration_node).
        """
        try:
            new_state = await self._vector.search(state)
            vector_slot = new_state.get("vector") or {}
            plan_slot = new_state.get("plan") or {}
            results = vector_slot.get("results") or []
            refined = plan_slot.get("refined_query")
            logger.info(
                "vector.results room=%s count=%d refined=%r",
                state.get("room_id"),
                len(results),
                (refined or "")[:40],
            )
            ret: dict[str, Any] = {
                "vector": {"results": vector_slot.get("results")},
                "plan": {"refined_query": refined},
                "node_path": ["vector_node"],
            }
            # VectorAgent 가 search_channels 를 채웠으면 전파한다.
            # 빈 dict 는 reducer 의 리셋 시그널이므로 포함하지 않는다.
            if channels := new_state.get("search_channels"):
                ret["search_channels"] = channels
            return ret
        except RateLimitException:
            raise
        except Exception as exc:
            logger.exception("vector_node 실행 오류")
            return {"error": str(exc), "node_path": ["vector_error"]}

    async def hydration_node(self, state: AgentState) -> dict[str, Any]:
        """검색 결과 service_id → 원본 데이터 통합 슬롯 매핑.

        sql_node / vector_node 직후, answer_node 직전에 실행된다.
        검색 노드별 출력 형식(sql_results / vector_results)을
        단일 슬롯 hydrated_services 로 통합하여 AnswerAgent 가 검색 경로에 의존하지
        않도록 한다.

        세션(노드 로컬, 0-6):
            data_session — public_service_reservations 원본 조회 전용 (on_data_reader).
            풀에서 잡고 조회 후 즉시 반납한다.

        answering progress emit 단일 지점:
            sql_node / vector_node 는 secondary_intent 팬아웃으로 동일 super-step 에
            병렬 실행될 수 있어 emit 주체가 될 수 없다(둘 다 emit → 2회 회귀). 검색 경로
            (단일 sql/vector·팬아웃·out_of_scope attribute_gap)가 모두 합류하는 단일
            머지점이 hydration_node 이므로, answering 은 여기서 1회만 emit 한다
            (emit_answering 가드 슬롯으로 retry 재진입까지 1회 보장). map_node /
            analytics_node 는 hydration 을 거치지 않고 answer_node 로 직행하므로 자체
            emit 을 유지한다(이 둘은 팬아웃 대상이 아니라 중복 없음).
        """
        guard = _emit.emit_answering(state)
        try:
            async with self._ondata.session() as data_session:
                update = await self._hydration(state, data_session)
            hydrated = (update.get("hydration") or {}).get("hydrated_services") or []
            logger.info(
                "hydration.done room=%s count=%d",
                state.get("room_id"),
                len(hydrated),
            )
            update["node_path"] = ["hydration_node"]
            update.update(guard)
            return update
        except Exception:
            logger.exception("hydration_node 실행 오류")
            return {
                "hydration": {"hydrated_services": []},
                "node_path": ["hydration_error"],
                **guard,
            }

    async def map_node(self, state: AgentState) -> dict[str, Any]:
        """map_search 호출 — map_results 설정.

        lat/lng 미제공 시 검색을 생략하고 map_results=None을 반환한다.
        라우팅은 항상 이 노드를 거치므로 map 분기 처리는 내부에서 담당한다.
        노드 로컬 세션(0-6): data_session 을 풀에서 잡고 검색 후 즉시 반납한다.
        """
        # 검색 노드 완료 → answering 단계로 (다른 검색 노드와 동일한 emit 시점).
        guard = _emit.emit_answering(state)
        lat = state.get("user_lat")
        lng = state.get("user_lng")
        if lat is not None and lng is not None:
            try:
                # MAP 0건 재시도 시 retry_prep_node 가 retry_radius_m 을 세팅한다.
                # 없으면 기본 반경(1000m). ChannelData 에도 실제 사용 반경을 반영한다.
                radius = state.get("retry_radius_m") or _MAP_DEFAULT_RADIUS_M
                geojson = await self._ondata.map_proximity(lat, lng, radius)
                features = (geojson or {}).get("features") or []
                channel_data = ChannelData(
                    kind=SearchKind.MAP,
                    query=ChannelQuery(
                        query_text=f"lat={lat},lng={lng},r={radius}m",
                        parameters={
                            "lat": lat,
                            "lng": lng,
                            "radius_m": radius,
                            "top_k": _MAP_TOP_K,
                        },
                    ),
                    hits=_to_hits(
                        [f["properties"] for f in features if "properties" in f],
                        score_field="distance_m",
                        meta_fn=lambda f: {"distance_m": f.get("distance_m")},
                    ),
                )
                return {
                    "map": {"results": geojson},
                    "search_channels": {SearchChannel.MAP: channel_data},
                    "node_path": ["map_node"],
                    **guard,
                }
            except Exception as exc:
                logger.exception("map_node 실행 오류")
                return {"error": str(exc), "node_path": ["map_error"], **guard}
        else:
            logger.warning("map_node — lat/lng 미제공, map_results=None 처리")
            return {"map": {"results": None}, "node_path": ["map_node"], **guard}

    async def analytics_node(self, state: AgentState) -> dict[str, Any]:
        """AnalyticsAgent.run() 호출 — analytics_results/group_by/metric 설정.

        집계는 on_data(data_session) 에서 수행한다. hydration 없이 answer_node 로 직행한다.
        search_channels 는 채우지 않으므로 search_persist_node 가 즉시 skip 한다.
        노드 로컬 세션(0-6): data_session 을 풀에서 잡고 집계 후 즉시 반납한다.

        graceful degrade:
            _AnalyticsParams Literal+validator 로 group_by 화이트리스트를 강제하지만,
            만일의 KeyError/DB 오류라도 미처리 500 으로 새지 않도록 예외를 잡아
            빈 결과 + error + node_path "analytics_error" 로 처리한다.
        """
        # 검색 노드 완료 → answering 단계로 (다른 검색 노드와 동일한 emit 시점).
        guard = _emit.emit_answering(state)
        try:
            async with self._ondata.session() as data_session:
                new_state = await self._analytics.run(state, data_session)
            analytics_slot = new_state.get("analytics") or {}
            rows = analytics_slot.get("results") or []
            logger.info(
                "analytics.results room=%s group_by=%s metric=%s count=%d",
                state.get("room_id"),
                analytics_slot.get("group_by"),
                analytics_slot.get("metric"),
                len(rows),
            )
            return {
                "analytics": {
                    "results": analytics_slot.get("results"),
                    "group_by": analytics_slot.get("group_by"),
                    "metric": analytics_slot.get("metric"),
                    "keyword": analytics_slot.get("keyword"),
                },
                "node_path": ["analytics_node"],
                **guard,
            }
        except Exception as exc:
            logger.exception("analytics_node 실행 오류")
            # error 를 세팅하면 _analytics_zero_hits 가 참이 되어 1회 재시도된다:
            # 결정적 error 라도 1회는 재시도해 일시 오류(DB 순단 등) 회복 기회를 준다.
            # 2회차는 retry_count 캡(self_correction_edge ①)으로 종료되므로 무한 루프 없음.
            return {
                "analytics": {"results": []},
                "error": str(exc),
                "node_path": ["analytics_error"],
                **guard,
            }
