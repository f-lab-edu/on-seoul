"""검색 페이즈 — sql/vector/map/analytics/hydration + rrf/게이트 노드·엣지."""

import logging
from typing import Any

from agents import _emit
from agents._helpers import assess_result_quality, reservation_guide_already_shown
from agents._ondata_gateway import OnDataReader, default_reader
from agents._search_channel_utils import _to_hits
from agents.analytics_agent import AnalyticsAgent
from agents.nodes._shared import is_gap_oos
from agents.hydration_node import HydrationNode
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
from schemas.state import ActionType, AgentState
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
    ) -> None:
        self._sql = sql
        self._vector = vector
        self._analytics = analytics
        self._hydration = hydration
        self._ondata = ondata or default_reader

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
        """C2 pre-answer 0건 게이트 + P2 결과 품질 자각 패스(B).

        hydration_node 직후 hydrated_services=[] 이면 answer_node를 미호출하고
        retry_prep_node로 직행하도록 엣지 로직(route_pre_answer_gate)에서 판정한다.

        P2 자각 패스(B): RETRIEVE(hydration 결과) 경로에서만 결과 성격(쏠림·빈약)을
        경량 휴리스틱으로 점검해 answer 가 소비할 평면 슬롯 result_quality 를 산출한다.
        재검색은 하지 않고(라우팅 불변, 전진 1회) answer 톤만 바꾼다. 통합회원 안내
        반복 억제용 reservation_guide_shown(history 상류 파싱, answer 는 bool 만 소비)도
        함께 적재한다. 점검 예외는 result_quality=None 으로 격리(best-effort).

        attribute_gap(OUT_OF_SCOPE)·describe·MAP·ANALYTICS 는 자각 패스 비대상이라
        result_quality 슬롯을 건드리지 않는다(None 유지).
        """
        result_quality: dict[str, Any] | None = None
        reservation_shown = False
        if self._is_retrieve_path(state):
            try:
                rows = state["hydration"].get("hydrated_services") or []
                result_quality = assess_result_quality(
                    rows, area_filter=state["filters"].get("area_name")
                )
                reservation_shown = reservation_guide_already_shown(
                    state.get("history")
                )
            except Exception:
                # best-effort: 점검 실패가 답변을 막지 않는다(현행 조립으로 폴백).
                logger.exception("pre_answer_gate 결과 품질 점검 실패")
                result_quality = None
        return {
            "result_quality": result_quality,
            "reservation_guide_shown": reservation_shown,
            "node_path": ["pre_answer_gate"],
        }

    @staticmethod
    def _is_retrieve_path(state: AgentState) -> bool:
        """자각 패스(B) 평가 대상 — 순수 RETRIEVE(hydration) 경로인지 판정한다.

        attribute_gap(OUT_OF_SCOPE)·describe·MAP·ANALYTICS 는 비대상이다.
        action=None 은 router fallback(검색 실행)이라 RETRIEVE 와 동일 취급한다.
        """
        return state["triage"].get("action") in (ActionType.RETRIEVE, None)

    def route_pre_answer_gate(self, state: AgentState) -> str:
        """C2 게이트 엣지: hydrated_services=[] 시 retry_prep, 그 외 answer_node.

        M1-a: gap(attribute_gap/operational_detail, OUT_OF_SCOPE)은 vector 검색을 실제
        실행하므로 RETRIEVE 와 동일한 0건 처리 대상이다. 그 외 비-RETRIEVE action 은
        게이트 통과(직접 answer).
        """
        action = state["triage"].get("action")
        oos_type = state["triage"].get("out_of_scope_type")
        # action=None 은 route_intake 의 else→router_node fallback(검색 실행)과
        # 대칭이라 RETRIEVE 와 동일 취급한다. 입구에서 검색을 돌려놓고 이 게이트만
        # 건너뛰면 빈 컨텍스트로 answer_node 에 진입하므로 None 도 검색 경로로 본다.
        # gap(attribute_gap/operational_detail)은 검색 경로라 0건 체크에 포함한다(중첩 경로).
        is_search_path = action in (ActionType.RETRIEVE, None) or (
            action == ActionType.OUT_OF_SCOPE and is_gap_oos(oos_type)
        )
        if not is_search_path:
            return "answer_node"

        hydrated = state["hydration"].get("hydrated_services")
        retry_count = state.get("retry_count", 0)

        # C2: hydrated_services=[] 이면 answer LLM 미호출 + retry_prep 직행
        # retry_count 캡(>=1) 시에는 answer_node로 통과(무한루프 방지)
        #
        # []=검색 실행·0건(→retry) vs None=hydration 미실행(→retry 무의미, 통과)을
        # 구분한다. hydration_node 는 예외/실패 시에도 hydrated_services 를 []로
        # 귀결시키므로(hydration_node.py 예외 처리 + 본 모듈 wrapper) 'hydration 실패'와
        # '진짜 0건'은 동일하게 []→retry_prep 로 묶인다(빈 컨텍스트 answer 진입 없음).
        # 게이트는 항상 hydration_node 뒤라 라이브 경로에서 None 은 도달하지 않으며,
        # is not None 가드는 부분-state/방어용이다.
        if hydrated is not None and len(hydrated) == 0 and retry_count == 0:
            return "retry_prep_node"

        return "answer_node"

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
