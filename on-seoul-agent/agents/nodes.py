"""AgentGraph 노드·엣지 구현 모음.

AgentGraph에서 노드/엣지 로직 책임을 분리한다.
노드 구현은 GraphNodes가, 그래프 조립과 실행은 AgentGraph가 담당한다.

변경 이유:
    AgentGraph가 그래프 조립, 노드 구현, 엣지 로직, 런타임 상태 관리,
    공개 실행 인터페이스라는 5가지 책임을 가졌다.
    GraphNodes 분리로 각 클래스의 변경 이유(reason to change)를 단일화한다.

세션·타이밍:
    GraphNodes 인스턴스는 AgentGraph가 소유하며,
    run()/stream() 진입 시 prepare()로 세션과 실행 상태를 초기화한다.

캐시 노드만 클래스로 분리된 이유:
    CacheCheckNode / CacheWriteNode는 Redis 의존성을 명시적으로 주입받고
    단위 테스트에서 격리성을 확보하기 위해 별도 호출 가능 객체로 분리한다.
    다른 노드는 RouterAgent/SqlAgent/VectorAgent/AnswerAgent에 위임하므로
    GraphNodes 메서드로 충분하다.
"""

import json
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents.analytics_agent import AnalyticsAgent
from agents.answer_agent import AnswerAgent
from agents.hydration_node import HydrationNode
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from core.cache import get_cached_answer, set_cached_answer
from core.config import settings
from core.exceptions import RateLimitException
from agents._search_channel_utils import _to_hits
from schemas.search import (
    RESET_CHANNELS,
    ChannelData,
    ChannelQuery,
    SearchChannel,
    SearchKind,
    kind_of,
)
from schemas.state import AgentState, IntentType
from tools.map_search import DEFAULT_RADIUS_M as _MAP_DEFAULT_RADIUS_M
from tools.map_search import TOP_K as _MAP_TOP_K
from tools.map_search import map_search
from tools.sql_search import TOP_K as _SQL_TOP_K

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# search_persist INSERT SQL
# ---------------------------------------------------------------------------
# 두 상수는 GraphNodes.search_persist_node 에서만 사용한다.
# ON CONFLICT DO NOTHING: 정상 흐름에서는 retry_prep_node 가 search_channels 를 리셋하므로
# UNIQUE 위반이 발생하지 않는다. 방어적 안전망.

_INSERT_SEARCH_QUERIES_SQL = """
INSERT INTO chat_search_queries (message_id, kind, channel, query_text, parameters)
VALUES (:message_id, :kind, :channel, :query_text, CAST(:parameters AS jsonb))
ON CONFLICT (message_id, channel) DO NOTHING
"""

_INSERT_SEARCH_RESULTS_SQL = """
INSERT INTO chat_search_results (message_id, kind, channel, rank, service_id, score, meta)
VALUES (:message_id, :kind, :channel, :rank, :service_id, :score, CAST(:meta AS jsonb))
ON CONFLICT (message_id, channel, rank) DO NOTHING
"""

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

# MAP 0건 완화 — 반경 확장(1회). 기본 1000m → 3000m.
_MAP_RETRY_RADIUS_M: int = 3000


class GraphNodes:
    """AgentGraph 노드·엣지 구현 (무상태).

    인스턴스는 AgentGraph.__init__()에서 1회 생성되어 프로세스 내에서 공유된다.
    제안 0(요청 격리): 요청별 가변 자원/상태를 인스턴스 속성으로 두지 않는다.
      - DB 세션 → RunnableConfig `configurable` 로 per-run 주입 (노드 메서드 인자).
      - node_path → AgentState 슬롯 (node_path_reducer 로 per-invoke 누적).
      - 시작 시각 → AgentState["started_at"].
    따라서 동시 요청이 같은 GraphNodes 를 공유해도 세션/경로 교차가 발생하지 않는다.
    """

    def __init__(
        self,
        router: RouterAgent,
        sql_agent: SqlAgent,
        vector_agent: VectorAgent,
        answer_agent: AnswerAgent,
        analytics_agent: AnalyticsAgent,
        redis: Any = None,
        hydration: HydrationNode | None = None,
    ) -> None:
        self._router = router
        self._sql = sql_agent
        self._vector = vector_agent
        self._answer = answer_agent
        self._analytics = analytics_agent
        self._hydration = hydration or HydrationNode()
        self._cache_check = CacheCheckNode(redis=redis)
        self._cache_write = CacheWriteNode(redis=redis)

    # ------------------------------------------------------------------
    # 노드 구현
    # ------------------------------------------------------------------

    async def router_node(self, state: AgentState) -> dict[str, Any]:
        """RouterAgent.classify() 호출 — intent · refined_query 설정.

        재시도 여부는 state["retry_count"] > 0으로 판단한다.
        이전 검색 결과 초기화와 retry_count 증가는 retry_prep_node에서 완료되므로
        이 노드는 순수하게 의도 분류만 담당한다.

        refined_query는 Router가 산출하여 후속 cache_check_node가 정확한
        키 기반 lookup을 수행할 수 있도록 한다. None이면 cache_check는
        pass-through되며 VectorAgent가 자체 refine 체인으로 대체 산출한다.

        forced_intent honor:
            retry_prep_node 가 방향성 재시도로 intent 를 강제하면 LLM 재분류를
            skip 하고 그 intent 를 그대로 반환한다. forced_intent 는 즉시 None 으로
            소비(1회성)하여 무한 전환을 막는다. refined_query/post-filter 는 채우지
            않으므로 cache_check 는 pass-through 되고(0건이던 원 질의 오hit 방지),
            전환된 경로(VECTOR)가 자체 정제한다.
        """
        forced = state.get("forced_intent")
        if forced is not None:
            logger.info(
                "router.forced room=%s intent=%s",
                state.get("room_id"),
                forced.value,
            )
            return {"intent": forced, "forced_intent": None, "node_path": ["router"]}
        try:
            result = await self._router.classify(
                state["message"],
                history=state.get("history") or [],
            )
            update: dict[str, Any] = {"intent": result.intent, "node_path": ["router"]}
            if result.refined_query is not None:
                update["refined_query"] = result.refined_query
            # post-filter는 추출 성공한 필드만 state로 전파한다.
            # None은 keys()에 포함시키지 않아 retry 경로에서 초기화된 값을
            # 무의미하게 덮어쓰지 않도록 한다.
            if result.max_class_name is not None:
                update["max_class_name"] = result.max_class_name
            if result.area_name is not None:
                update["area_name"] = result.area_name
            if result.service_status is not None:
                update["service_status"] = result.service_status
            if result.payment_type is not None:
                update["payment_type"] = result.payment_type
            if result.vector_sub_intent is not None:
                update["vector_sub_intent"] = result.vector_sub_intent
            logger.info(
                "router.classify room=%s intent=%s refined=%r "
                "max_class=%s area=%s status=%s",
                state.get("room_id"),
                result.intent.value,
                (result.refined_query or "")[:40],
                result.max_class_name,
                result.area_name,
                result.service_status,
            )
            return update
        except Exception as exc:
            logger.exception("router_node 실행 오류")
            return {
                "error": str(exc),
                "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                "node_path": ["router_error"],
            }

    async def retry_prep_node(self, state: AgentState) -> dict[str, Any]:
        """자기 교정 재시도 준비 노드 (intent별 방향성 분기).

        _self_correction_edge에서 재시도가 결정될 때만 실행된다.
        retry_count를 1 증가시키고 intent에 따라 전환/완화/반경확장을 수행한다.

        분기:
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
        intent = state.get("intent")
        logger.info(
            "retry.triggered room=%s retry_count=%d intent=%s",
            state.get("room_id"),
            new_retry_count,
            intent.value if intent else None,
        )

        # 모든 분기 공통 베이스 — 분기별 override 로 검색 슬롯/필터를 덮어쓴다.
        update: dict[str, Any] = {
            "retry_count": new_retry_count,
            "error": None,
            "retry_relaxed": True,
            "search_channels": RESET_CHANNELS,
            "node_path": ["retry_prep"],
        }

        # 케이스 A: 강제 전환 대상 intent (SQL_SEARCH → VECTOR_SEARCH 등)
        fallback = _RETRY_FALLBACK_INTENT.get(intent) if intent else None
        if fallback is not None:
            update.update(
                {
                    "forced_intent": fallback,
                    "sql_results": None,
                    "vector_results": None,
                    "map_results": None,
                    "hydrated_services": None,
                    "refined_query": None,
                    # 전환 시 정형 필터는 유지하지 않는다(전환 경로가 자체 정제).
                    "max_class_name": None,
                    "area_name": None,
                    "service_status": None,
                    "payment_type": None,
                }
            )
            return update

        # 케이스 B: ANALYTICS — 가장 제약 큰 effective 필터 1개만 드롭(intent 유지)
        if intent == IntentType.ANALYTICS:
            update["analytics_results"] = None
            for field in _ANALYTICS_DROP_ORDER:
                if state.get(field):
                    update[field] = None  # 한 개만 드롭하고 중단
                    break
            return update

        # 케이스 D: MAP — 반경 확장(intent 유지)
        # 케이스 C 와 달리 sql/vector/hydrated 슬롯을 건드리지 않는다: MAP 경로는
        # 이 슬롯들을 채우지 않으므로 리셋 자체가 무의미하다(반경만 확장하면 충분).
        if intent == IntentType.MAP:
            update.update(
                {
                    "map_results": None,
                    # map_node 가 이 값을 기본 반경 대신 사용한다.
                    "retry_radius_m": _MAP_RETRY_RADIUS_M,
                }
            )
            return update

        # 케이스 C: 기존 완화 (VECTOR_SEARCH 0건, 빈 답변 등)
        # payment_type 완화 — 0건 재시도 시 결제 유형 필터를 드롭한다.
        update.update(
            {
                "sql_results": None,
                "vector_results": None,
                "map_results": None,
                "hydrated_services": None,
                "refined_query": None,
                "max_class_name": None,
                "area_name": None,
                "service_status": None,
                "payment_type": None,
            }
        )
        return update

    async def sql_node(
        self, state: AgentState, data_session: AsyncSession
    ) -> dict[str, Any]:
        """SqlAgent.search() 호출 — sql_results + search_channels 설정."""
        try:
            new_state = await self._sql.search(state, data_session)
            sql_rows = new_state.get("sql_results") or []
            keyword = new_state.get("sql_keyword")
            logger.info(
                "sql.results room=%s count=%d", state.get("room_id"), len(sql_rows)
            )

            channel_data = ChannelData(
                kind=SearchKind.SQL,
                query=ChannelQuery(
                    query_text=keyword,
                    parameters={
                        "max_class_name": state.get("max_class_name"),
                        "area_name": state.get("area_name"),
                        "service_status": state.get("service_status"),
                        "payment_type": state.get("payment_type"),
                        "keyword": keyword,
                        "top_k": _SQL_TOP_K,
                    },
                ),
                hits=_to_hits(sql_rows, score_field=None),
            )
            return {
                "sql_results": new_state.get("sql_results"),
                "sql_keyword": keyword,
                "search_channels": {SearchChannel.SQL: channel_data},
                "node_path": ["sql_node"],
            }
        except Exception as exc:
            logger.exception("sql_node 실행 오류")
            return {"error": str(exc), "node_path": ["sql_error"]}

    async def vector_node(
        self, state: AgentState, ai_session: AsyncSession
    ) -> dict[str, Any]:
        """VectorAgent.search() 호출 — vector_results(메타데이터 only), refined_query 설정.

        hydration(원본 조회)은 후속 hydration_node 가 담당하므로
        ai_session 만 전달한다.
        """
        try:
            new_state = await self._vector.search(state, ai_session)
            results = new_state.get("vector_results") or []
            logger.info(
                "vector.results room=%s count=%d refined=%r",
                state.get("room_id"),
                len(results),
                (new_state.get("refined_query") or "")[:40],
            )
            ret: dict[str, Any] = {
                "vector_results": new_state.get("vector_results"),
                "refined_query": new_state.get("refined_query"),
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

    async def hydration_node(
        self, state: AgentState, data_session: AsyncSession
    ) -> dict[str, Any]:
        """검색 결과 service_id → 원본 데이터 통합 슬롯 매핑.

        sql_node / vector_node 직후, answer_node 직전에 실행된다.
        검색 노드별 출력 형식(sql_results / vector_results)을
        단일 슬롯 hydrated_services 로 통합하여 AnswerAgent 가 검색 경로에 의존하지
        않도록 한다.

        세션:
            data_session — public_service_reservations 원본 조회 전용 (on_data_reader)
        """
        try:
            update = await self._hydration(state, data_session)
            hydrated = update.get("hydrated_services") or []
            logger.info(
                "hydration.done room=%s count=%d",
                state.get("room_id"),
                len(hydrated),
            )
            update["node_path"] = ["hydration_node"]
            return update
        except Exception:
            logger.exception("hydration_node 실행 오류")
            return {"hydrated_services": [], "node_path": ["hydration_error"]}

    async def map_node(
        self, state: AgentState, data_session: AsyncSession
    ) -> dict[str, Any]:
        """map_search 호출 — map_results 설정.

        lat/lng 미제공 시 검색을 생략하고 map_results=None을 반환한다.
        라우팅은 항상 이 노드를 거치므로 map 분기 처리는 내부에서 담당한다.
        """
        lat = state.get("user_lat")
        lng = state.get("user_lng")
        if lat is not None and lng is not None:
            try:
                # MAP 0건 재시도 시 retry_prep_node 가 retry_radius_m 을 세팅한다.
                # 없으면 기본 반경(1000m). ChannelData 에도 실제 사용 반경을 반영한다.
                radius = state.get("retry_radius_m") or _MAP_DEFAULT_RADIUS_M
                geojson = await map_search(data_session, lat, lng, radius_m=radius)
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
                    "map_results": geojson,
                    "search_channels": {SearchChannel.MAP: channel_data},
                    "node_path": ["map_node"],
                }
            except Exception as exc:
                logger.exception("map_node 실행 오류")
                return {"error": str(exc), "node_path": ["map_error"]}
        else:
            logger.warning("map_node — lat/lng 미제공, map_results=None 처리")
            return {"map_results": None, "node_path": ["map_node"]}

    async def analytics_node(
        self, state: AgentState, data_session: AsyncSession
    ) -> dict[str, Any]:
        """AnalyticsAgent.run() 호출 — analytics_results/group_by/metric 설정.

        집계는 on_data(data_session) 에서 수행한다. hydration 없이 answer_node 로 직행한다.
        search_channels 는 채우지 않으므로 search_persist_node 가 즉시 skip 한다.

        graceful degrade:
            _AnalyticsParams Literal+validator 로 group_by 화이트리스트를 강제하지만,
            만일의 KeyError/DB 오류라도 미처리 500 으로 새지 않도록 예외를 잡아
            빈 결과 + error + node_path "analytics_error" 로 처리한다.
        """
        try:
            new_state = await self._analytics.run(state, data_session)
            rows = new_state.get("analytics_results") or []
            logger.info(
                "analytics.results room=%s group_by=%s metric=%s count=%d",
                state.get("room_id"),
                new_state.get("analytics_group_by"),
                new_state.get("analytics_metric"),
                len(rows),
            )
            return {
                "analytics_results": new_state.get("analytics_results"),
                "analytics_group_by": new_state.get("analytics_group_by"),
                "analytics_metric": new_state.get("analytics_metric"),
                "analytics_keyword": new_state.get("analytics_keyword"),
                "node_path": ["analytics_node"],
            }
        except Exception as exc:
            logger.exception("analytics_node 실행 오류")
            # error 를 세팅하면 _analytics_zero_hits 가 참이 되어 1회 재시도된다:
            # 결정적 error 라도 1회는 재시도해 일시 오류(DB 순단 등) 회복 기회를 준다.
            # 2회차는 retry_count 캡(self_correction_edge ①)으로 종료되므로 무한 루프 없음.
            return {
                "analytics_results": [],
                "error": str(exc),
                "node_path": ["analytics_error"],
            }

    async def answer_node(self, state: AgentState) -> dict[str, Any]:
        """AnswerAgent.answer() 호출 — answer, title 설정."""
        if state.get("error") and state.get("answer"):
            return {"node_path": ["answer_node"]}

        try:
            new_state = await self._answer.answer(state)
            answer = new_state.get("answer") or ""
            logger.info(
                "answer.generated room=%s len=%d", state.get("room_id"), len(answer)
            )
            # 관측: 검색 결과는 있는데 카드가 비어 있으면 normalize 무음 실패 신호.
            # 동작은 바꾸지 않고 경고만 남긴다.
            intent = state.get("intent")
            if intent in (IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH):
                hydrated = state.get("hydrated_services") or []
                sql_results = state.get("sql_results") or []
                if (hydrated or sql_results) and not new_state.get("service_cards"):
                    logger.warning(
                        "answer.cards_empty_with_results room=%s intent=%s "
                        "hydrated=%d sql=%d",
                        state.get("room_id"),
                        getattr(intent, "value", intent),
                        len(hydrated),
                        len(sql_results),
                    )
            return {
                "answer": new_state.get("answer"),
                "title": new_state.get("title"),
                "service_cards": new_state.get("service_cards"),
                "node_path": ["answer_node"],
            }
        except Exception as exc:
            logger.exception("answer_node 실행 오류")
            return {
                "error": str(exc),
                "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                "node_path": ["answer_error"],
            }

    async def cache_check_node(self, state: AgentState) -> dict[str, Any]:
        """router 직후 cache 조회 — hit 시 state 복원, cache_hit 플래그 설정."""
        result = await self._cache_check(state)
        if result.get("cache_hit"):
            result["node_path"] = ["cache_check_hit"]
        else:
            result["node_path"] = ["cache_check_miss"]
        return result

    async def cache_write_node(self, state: AgentState) -> dict[str, Any]:
        """answer 직후 정상 결과만 캐싱 (skip 조건은 노드 내부 처리)."""
        result = await self._cache_write(state)
        result["node_path"] = ["cache_write"]
        return result

    async def search_persist_node(
        self, state: AgentState, ai_session: AsyncSession
    ) -> dict[str, Any]:
        """chat_search_queries + chat_search_results 일괄 적재 (best-effort 종단 노드).

        AgentState.search_channels 를 순회하여 두 테이블에 동일 트랜잭션으로 INSERT.

        best-effort 정책:
          - INSERT 실패는 그래프 결과에 영향 없음 (logger.warning + rollback + return {})
          - 빈 채널 맵(search_channels={}) 이면 INSERT 없이 즉시 return {}
          - hits 가 비어도 query 행은 기록 — "검색했는데 결과 없음" 도 분석 가치 있음
          - 두 테이블은 같은 트랜잭션 — 한쪽만 커밋되는 불일관 방지

        ON CONFLICT DO NOTHING:
          self-correction 재시도 시 retry_prep_node 가 search_channels 를 {} 로 리셋하므로
          정상 흐름에서 UNIQUE 위반은 발생하지 않는다. 방어적 안전망으로만 사용된다.

        세션 공유:
          ai_session 은 trace_node 와 공유한다(config 로 per-run 주입). 정상 흐름
          (commit 성공)과 INSERT 실패 후 rollback 성공 시에는 trace_node 진입 전 세션이
          clean 상태다. 단, rollback 자체가 실패하면(커넥션 단절 등) 세션이 dirty인 채
          trace_node 로 넘어가 trace INSERT 도 실패할 수 있다. `_save_trace` 는 자체
          except + rollback 핸들러를 보유하므로 워크플로우 결과에는 영향이 없으나, 두
          관측 데이터가 동시 유실될 수 있다. 완전한 독립성이 필요해지면 trace_node 전용
          세션 분리를 검토할 것.
        """
        channels: dict[str, ChannelData] = state.get("search_channels") or {}
        if not channels:
            return {"node_path": ["search_persist_skip"]}

        message_id = state["message_id"]
        query_rows: list[dict] = []
        result_rows: list[dict] = []

        for channel_name, data in channels.items():
            # 알려진 채널: kind_of() 로 정규 kind 를 결정 (ChannelData.kind 불일치 방지).
            # 미등록 채널(freeform): ChannelData.kind 를 caller 책임으로 그대로 사용.
            # DB CHECK 제약이 최종 안전망 역할을 하며, 위반 시 best-effort 핸들러에서 포착된다.
            try:
                kind = kind_of(channel_name)
            except ValueError:
                kind = data["kind"]
            q = data["query"]
            hits = data["hits"]  # ChannelData.hits 는 필수 키

            query_rows.append(
                {
                    "message_id": message_id,
                    "kind": kind,
                    "channel": channel_name,
                    "query_text": q[
                        "query_text"
                    ],  # ChannelQuery 필수 키 (값은 None 허용)
                    "parameters": json.dumps(q["parameters"] or {}, default=str),
                }
            )

            for hit in hits:
                result_rows.append(
                    {
                        "message_id": message_id,
                        "kind": kind,
                        "channel": channel_name,
                        "rank": hit["rank"],
                        "service_id": hit["service_id"],
                        "score": hit["score"],  # ChannelHit 필수 키 (값은 None 허용)
                        "meta": json.dumps(hit["meta"] or {}, default=str),
                    }
                )

        try:
            if query_rows:
                await ai_session.execute(
                    text(_INSERT_SEARCH_QUERIES_SQL),
                    query_rows,
                )
            if result_rows:
                await ai_session.execute(
                    text(_INSERT_SEARCH_RESULTS_SQL),
                    result_rows,
                )
            await ai_session.commit()
            logger.info(
                "search_persist.done message_id=%s queries=%d results=%d",
                message_id,
                len(query_rows),
                len(result_rows),
            )
            return {"node_path": ["search_persist"]}
        except Exception:
            logger.warning(
                "search_persist 적재 실패 (message_id=%s)", message_id, exc_info=True
            )
            try:
                await ai_session.rollback()
            except Exception:
                pass
            return {"node_path": ["search_persist_error"]}

    async def trace_node(
        self, state: AgentState, ai_session: AsyncSession
    ) -> dict[str, Any]:
        """chat_agent_traces 저장 (best-effort 종단 노드)."""
        started_at = state.get("started_at") or time.monotonic()
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        # node_path: trace_node 자신은 아직 누적되지 않았으므로 state 의 누적분 + "trace".
        node_path = list(state.get("node_path") or []) + ["trace"]
        trace_payload: dict[str, Any] = {
            "intent": state.get("intent"),
            "node_path": node_path,
            "elapsed_ms": elapsed_ms,
            "error": state.get("error"),
        }
        # ANALYTICS 관측치는 chat_search_results(service_id/score) 스키마에 맞지 않으므로
        # trace(JSONB) 확장으로 저장한다 (마이그레이션 없이, §4-4.1).
        if state.get("intent") == IntentType.ANALYTICS:
            analytics_rows = state.get("analytics_results") or []
            trace_payload["analytics"] = {
                "group_by": state.get("analytics_group_by"),
                "metric": state.get("analytics_metric"),
                "filters": {
                    "max_class_name": state.get("max_class_name"),
                    "area_name": state.get("area_name"),
                    "service_status": state.get("service_status"),
                    "keyword": state.get("analytics_keyword"),
                },
                "result_count": len(analytics_rows),
                "result": analytics_rows,
            }
        await _save_trace(ai_session, state["message_id"], trace_payload)
        return {"trace": trace_payload, "node_path": ["trace"]}

    # ------------------------------------------------------------------
    # 엣지 로직
    # ------------------------------------------------------------------

    def post_cache_check(self, state: AgentState) -> str:
        """cache_check 직후 라우팅 — hit 시 search_persist_node → trace 경로, miss면 intent 분기.

        cache hit 시 검색이 수행되지 않으므로 search_channels 는 {} 상태다.
        search_persist_node 는 빈 채널 맵에서 즉시 skip 하고 return {} 하므로
        성능 오버헤드는 없다. 명시적으로 경유함으로써 종단 체인
        (cache_write → search_persist → trace) 의 일관성을 유지한다.

        NOTE: 직접 trace_node 로 라우팅하면 나중에 cache-hit 경로에서도 채널 데이터가
        존재하는 케이스가 생길 때 search_persist 가 묵묵히 스킵되는 latent bug 가 된다.
        """
        if state.get("cache_hit"):
            return "search_persist_node"
        return self.route_by_intent(state)

    def route_by_intent(self, state: AgentState) -> str:
        """intent 값에 따라 다음 노드를 결정한다."""
        error = state.get("error")
        answer = state.get("answer") or ""

        # router_node 예외 시 fallback_answer + error가 모두 설정됨.
        # intent가 None이므로 아래 else 분기가 동일하게 처리하지만, 의도 명시용 early-return.
        if error and answer.strip():
            return "answer_node"

        intent = state.get("intent")
        if intent == IntentType.SQL_SEARCH:
            return "sql_node"
        elif intent == IntentType.VECTOR_SEARCH:
            return "vector_node"
        elif intent == IntentType.MAP:
            return "map_node"
        elif intent == IntentType.ANALYTICS:
            return "analytics_node"
        else:
            return "answer_node"

    def self_correction_edge(self, state: AgentState) -> str:
        """answer_node 완료 후 자기 교정 여부를 결정한다.

        평가 순서(고정) — 다중 조건 동시 참 시 비결정성을 제거한다. 위에서부터
        먼저 매칭되는 하나만 적용(1회 캡이므로 단일 완화):
          ① retry_count 캡: 이미 1회 소진 → 종료(무한 루프 방지).
          ② 빈 답변: intent 무관 최우선 재시도(기존 동작).
          ③ intent별 0건:
             - SQL_SEARCH/VECTOR_SEARCH → _hard_filter_zero_hits
             - ANALYTICS               → _analytics_zero_hits
             - MAP                     → _map_zero_hits

        intent 분기는 상호배타라 한 순회에 하나만 평가된다. retry_prep_node 가
        retry_count 를 1 로 올리므로 다음 순회에서는 ①에서 즉시 종료된다.
        """
        retry_count = state.get("retry_count", 0)
        if retry_count != 0:
            return "end_normal"  # ① 캡

        answer = state.get("answer") or ""
        if not answer.strip():
            return "retry_prep_node"  # ② 빈 답변 (최우선, intent 무관)

        intent = state.get("intent")  # ③ intent별 0건
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
            state.get("hydrated_services")
            or state.get("sql_results")
            or state.get("vector_results")
        )

    @staticmethod
    def _analytics_zero_hits(state: AgentState) -> bool:
        """ANALYTICS 결과가 없거나(0행) error 인지 판정한다."""
        if state.get("error"):
            return True
        return not state.get("analytics_results")  # [] / None 모두 True

    @staticmethod
    def _map_zero_hits(state: AgentState) -> bool:
        """MAP 반경 내 0건인지 판정한다.

        lat/lng 미제공(map_results=None)은 위치 안내가 최선이므로 재시도 제외.
        features=[] (반경 내 0건)만 반경 확장 재시도 대상이다.
        """
        mr = state.get("map_results")
        if mr is None:
            return False
        return not (mr.get("features") or [])


# ---------------------------------------------------------------------------
# Trace 저장 헬퍼
# ---------------------------------------------------------------------------


async def _save_trace(
    session: AsyncSession,
    message_id: int,
    trace: dict[str, Any],
) -> None:
    """chat_agent_traces 테이블에 실행 메타데이터를 저장한다."""
    try:
        trace_json = json.dumps(trace, ensure_ascii=False, default=str)
        await session.execute(
            text(
                "INSERT INTO chat_agent_traces (message_id, trace) "
                "VALUES (:message_id, CAST(:trace AS jsonb))"
            ),
            {"message_id": message_id, "trace": trace_json},
        )
        await session.commit()
    except Exception as exc:
        logger.warning("trace 저장 실패 (message_id=%s): %s", message_id, exc)
        try:
            await session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Answer Cache 노드 (router 직후 / answer 직후)
# ---------------------------------------------------------------------------


class CacheCheckNode:
    """router 직후 — intent가 캐싱 대상이면 refined_query 기반으로 cache 조회.

    hit이면 state에 payload + 검색 결과 envelope를 복원하여 cache_hit=True로 표시한다.
    이후 라우팅은 graph 측 conditional edge에서 cache_hit으로 END 분기를 선택한다.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        intent = state.get("intent")
        refined = state.get("refined_query")
        if intent is None or refined is None:
            return {"cache_hit": False}
        if intent.value not in settings.answer_cache_eligible_intents:
            return {"cache_hit": False}

        max_class_name = state.get("max_class_name")
        area_name = state.get("area_name")
        service_status = state.get("service_status")
        payment_type = state.get("payment_type")

        envelope = await get_cached_answer(
            refined,
            self._redis,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
        )
        if envelope is None:
            logger.info(
                "cache.miss room=%s intent=%s refined=%r",
                state.get("room_id"),
                intent.value,
                refined[:40],
            )
            return {"cache_hit": False}

        payload = envelope.get("payload", {}) or {}
        snap = envelope.get("state", {}) or {}
        logger.info(
            "cache.hit room=%s intent=%s refined=%r",
            state.get("room_id"),
            intent.value,
            refined[:40],
        )
        return {
            "answer": payload.get("answer"),
            "title": payload.get("title"),
            # service_cards 는 payload 에 저장된다 (답변 결과물, search snapshot 아님).
            # 구버전 envelope (키 미존재) 는 None 폴백 —
            # routers/chat.py final payload 직렬화 단의 `or []` 가
            # 빈 배열로 안전하게 노출한다.
            "service_cards": payload.get("service_cards"),
            "vector_results": snap.get("vector_results"),
            "sql_results": snap.get("sql_results"),
            # hydrated_services 도 envelope 에 포함되어 있으면 복원한다.
            # 미보유 envelope(구버전 캐시 엔트리) 인 경우 None — AnswerAgent 가 폴백 처리.
            "hydrated_services": snap.get("hydrated_services"),
            "max_class_name": snap.get("max_class_name"),
            "area_name": snap.get("area_name"),
            "service_status": snap.get("service_status"),
            "payment_type": snap.get("payment_type"),
            "cache_hit": True,
        }


class CacheWriteNode:
    """answer 직후 — 정상 결과만 캐싱 (SQL_SEARCH / VECTOR_SEARCH).

    skip 조건: error / cache_hit / non-eligible intent / answer or refined 누락.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def __call__(self, state: AgentState) -> dict[str, Any]:
        if state.get("error"):
            return {}
        if state.get("cache_hit"):
            return {}
        intent = state.get("intent")
        if intent is None or intent.value not in settings.answer_cache_eligible_intents:
            return {}
        refined = state.get("refined_query")
        answer = state.get("answer")
        if not refined or not answer:
            return {}

        max_class_name = state.get("max_class_name")
        area_name = state.get("area_name")
        service_status = state.get("service_status")
        payment_type = state.get("payment_type")

        payload = {
            "message_id": state.get("message_id"),
            "answer": answer,
            "intent": intent.value,
            "title": state.get("title"),
            # 답변 결과물 — cache hit 시 프론트 카드 UI 가 다시 사용할 수 있도록 보존.
            # snap 이 아닌 payload 에 두는 이유: search snapshot 이 아니라 LLM 답변과 함께
            # 같은 라이프사이클로 묶이는 결과물이기 때문.
            "service_cards": state.get("service_cards"),
        }
        snap = {
            "refined_query": refined,
            "max_class_name": max_class_name,
            "area_name": area_name,
            "service_status": service_status,
            "payment_type": payment_type,
            "vector_results": state.get("vector_results"),
            "sql_results": state.get("sql_results"),
            # HydrationNode 가 채운 통합 슬롯 — cache hit 시 hydration 라운드트립 절감.
            "hydrated_services": state.get("hydrated_services"),
        }
        await set_cached_answer(
            refined,
            payload,
            snap,
            self._redis,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
        )
        empty = not snap["vector_results"] and not snap["sql_results"]
        logger.info("cache.write intent=%s empty=%s", intent.value, empty)
        return {}
