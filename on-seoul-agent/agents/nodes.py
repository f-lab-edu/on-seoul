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

from agents.answer_agent import AnswerAgent
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.vector_agent import VectorAgent
from core.cache import get_cached_answer, set_cached_answer
from core.config import settings
from agents._search_channel_utils import _to_hits
from schemas.search import ChannelData, ChannelQuery, SearchChannel, SearchKind, kind_of
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


class GraphNodes:
    """AgentGraph 노드·엣지 구현.

    인스턴스는 AgentGraph.__init__()에서 생성되며,
    run()/stream() 진입마다 prepare()로 런타임 상태를 초기화한다.
    """

    def __init__(
        self,
        router: RouterAgent,
        sql_agent: SqlAgent,
        vector_agent: VectorAgent,
        answer_agent: AnswerAgent,
        redis: Any = None,
    ) -> None:
        self._router = router
        self._sql = sql_agent
        self._vector = vector_agent
        self._answer = answer_agent
        self._cache_check = CacheCheckNode(redis=redis)
        self._cache_write = CacheWriteNode(redis=redis)

        # 런타임 상태 — prepare()로 매 요청마다 초기화된다.
        self.data_session: AsyncSession | None = None
        self.ai_session: AsyncSession | None = None
        self.node_path: list[str] = []
        self._start: float = 0.0

    def prepare(
        self,
        data_session: AsyncSession,
        ai_session: AsyncSession,
    ) -> None:
        """요청 진입 시 런타임 상태를 초기화한다."""
        self.data_session = data_session
        self.ai_session = ai_session
        self.node_path = []
        self._start = time.monotonic()

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
        """
        try:
            result = await self._router.classify(
                state["message"],
                recent_queries=state.get("recent_queries") or [],
            )
            self.node_path.append("router")
            update: dict[str, Any] = {"intent": result.intent}
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
            self.node_path.append("router_error")
            return {
                "error": str(exc),
                "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            }

    async def retry_prep_node(self, state: AgentState) -> dict[str, Any]:
        """자기 교정 재시도 준비 노드.

        _self_correction_edge에서 answer가 비어 재시도가 결정될 때만 실행된다.
        retry_count를 1 증가시키고 이전 검색 결과를 초기화한다.
        이 노드에서 초기화가 완료되므로 router_node는 분류에만 집중한다.
        """
        new_retry_count = (state.get("retry_count") or 0) + 1
        logger.info("retry.triggered room=%s retry_count=%d", state.get("room_id"), new_retry_count)
        self.node_path.append("retry_prep")
        return {
            "retry_count": new_retry_count,
            "error": None,
            "sql_results": None,
            "vector_results": None,
            "map_results": None,
            "refined_query": None,
            "max_class_name": None,
            "area_name": None,
            "service_status": None,
            # search_channels_reducer 에서 {} 를 리셋 시그널로 처리한다.
            # 이전 시도 채널 데이터를 지워 UNIQUE (message_id, channel) 위반을 방지.
            "search_channels": {},
        }

    async def sql_node(self, state: AgentState) -> dict[str, Any]:
        """SqlAgent.search() 호출 — sql_results + search_channels 설정."""
        assert self.data_session is not None
        try:
            new_state = await self._sql.search(state, self.data_session)
            self.node_path.append("sql_node")
            sql_rows = new_state.get("sql_results") or []
            keyword = new_state.get("sql_keyword")
            logger.info("sql.results room=%s count=%d", state.get("room_id"), len(sql_rows))

            channel_data = ChannelData(
                kind=SearchKind.SQL,
                query=ChannelQuery(
                    query_text=keyword,
                    parameters={
                        "max_class_name": state.get("max_class_name"),
                        "area_name": state.get("area_name"),
                        "service_status": state.get("service_status"),
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
            }
        except Exception as exc:
            logger.exception("sql_node 실행 오류")
            self.node_path.append("sql_error")
            return {"error": str(exc)}

    async def vector_node(self, state: AgentState) -> dict[str, Any]:
        """VectorAgent.search() 호출 — vector_results, refined_query 설정.

        VectorAgent는 임베딩 검색(ai_session)과 원본 hydration(data_session)을
        모두 수행하므로 두 세션을 모두 전달한다.
        """
        assert self.ai_session is not None
        assert self.data_session is not None
        try:
            new_state = await self._vector.search(
                state, self.ai_session, self.data_session
            )
            self.node_path.append("vector_node")
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
            }
            # VectorAgent 가 search_channels 를 채웠으면 전파한다.
            # 빈 dict 는 reducer 의 리셋 시그널이므로 포함하지 않는다.
            if channels := new_state.get("search_channels"):
                ret["search_channels"] = channels
            return ret
        except Exception as exc:
            logger.exception("vector_node 실행 오류")
            self.node_path.append("vector_error")
            return {"error": str(exc)}

    async def map_node(self, state: AgentState) -> dict[str, Any]:
        """map_search 호출 — map_results 설정.

        lat/lng 미제공 시 검색을 생략하고 map_results=None을 반환한다.
        라우팅은 항상 이 노드를 거치므로 map 분기 처리는 내부에서 담당한다.
        """
        assert self.data_session is not None
        lat = state.get("user_lat")
        lng = state.get("user_lng")
        if lat is not None and lng is not None:
            try:
                geojson = await map_search(self.data_session, lat, lng)
                self.node_path.append("map_node")
                features = (geojson or {}).get("features") or []
                channel_data = ChannelData(
                    kind=SearchKind.MAP,
                    query=ChannelQuery(
                        query_text=f"lat={lat},lng={lng},r={_MAP_DEFAULT_RADIUS_M}m",
                        parameters={
                            "lat": lat,
                            "lng": lng,
                            "radius_m": _MAP_DEFAULT_RADIUS_M,
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
                }
            except Exception as exc:
                logger.exception("map_node 실행 오류")
                self.node_path.append("map_error")
                return {"error": str(exc)}
        else:
            logger.warning("map_node — lat/lng 미제공, map_results=None 처리")
            self.node_path.append("map_node")
            return {"map_results": None}

    async def answer_node(self, state: AgentState) -> dict[str, Any]:
        """AnswerAgent.answer() 호출 — answer, title 설정."""
        if state.get("error") and state.get("answer"):
            self.node_path.append("answer_node")
            return {}

        try:
            new_state = await self._answer.answer(state)
            self.node_path.append("answer_node")
            answer = new_state.get("answer") or ""
            logger.info("answer.generated room=%s len=%d", state.get("room_id"), len(answer))
            return {
                "answer": new_state.get("answer"),
                "title": new_state.get("title"),
            }
        except Exception as exc:
            logger.exception("answer_node 실행 오류")
            self.node_path.append("answer_error")
            return {
                "error": str(exc),
                "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            }

    async def cache_check_node(self, state: AgentState) -> dict[str, Any]:
        """router 직후 cache 조회 — hit 시 state 복원, cache_hit 플래그 설정."""
        result = await self._cache_check(state)
        if result.get("cache_hit"):
            self.node_path.append("cache_check_hit")
        else:
            self.node_path.append("cache_check_miss")
        return result

    async def cache_write_node(self, state: AgentState) -> dict[str, Any]:
        """answer 직후 정상 결과만 캐싱 (skip 조건은 노드 내부 처리)."""
        result = await self._cache_write(state)
        self.node_path.append("cache_write")
        return result

    async def search_persist_node(self, state: AgentState) -> dict[str, Any]:
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
          self.ai_session 은 trace_node 와 공유한다. 이 노드는 항상 commit() 또는
          rollback() 으로 트랜잭션을 닫으므로 trace_node 진입 시 세션은 항상 clean 상태다.
        """
        assert self.ai_session is not None
        channels: dict[str, ChannelData] = state.get("search_channels") or {}
        if not channels:
            self.node_path.append("search_persist_skip")
            return {}

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

            query_rows.append({
                "message_id": message_id,
                "kind": kind,
                "channel": channel_name,
                "query_text": q["query_text"],   # ChannelQuery 필수 키 (값은 None 허용)
                "parameters": json.dumps(q["parameters"] or {}, default=str),
            })

            for hit in hits:
                result_rows.append({
                    "message_id": message_id,
                    "kind": kind,
                    "channel": channel_name,
                    "rank": hit["rank"],
                    "service_id": hit["service_id"],
                    "score": hit["score"],        # ChannelHit 필수 키 (값은 None 허용)
                    "meta": json.dumps(hit["meta"] or {}, default=str),
                })

        try:
            if query_rows:
                await self.ai_session.execute(
                    text(_INSERT_SEARCH_QUERIES_SQL),
                    query_rows,
                )
            if result_rows:
                await self.ai_session.execute(
                    text(_INSERT_SEARCH_RESULTS_SQL),
                    result_rows,
                )
            await self.ai_session.commit()
            self.node_path.append("search_persist")
            logger.info(
                "search_persist.done message_id=%s queries=%d results=%d",
                message_id, len(query_rows), len(result_rows),
            )
        except Exception:
            logger.warning(
                "search_persist 적재 실패 (message_id=%s)", message_id, exc_info=True
            )
            try:
                await self.ai_session.rollback()
            except Exception:
                pass
            self.node_path.append("search_persist_error")

        return {}

    async def trace_node(self, state: AgentState) -> dict[str, Any]:
        """chat_agent_traces 저장 (best-effort 종단 노드)."""
        assert self.ai_session is not None
        elapsed_ms = int((time.monotonic() - self._start) * 1000)
        trace_payload: dict[str, Any] = {
            "intent": state.get("intent"),
            "node_path": list(self.node_path),
            "elapsed_ms": elapsed_ms,
            "error": state.get("error"),
        }
        await _save_trace(self.ai_session, state["message_id"], trace_payload)
        return {"trace": trace_payload}

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
        else:
            return "answer_node"

    def self_correction_edge(self, state: AgentState) -> str:
        """answer_node 완료 후 자기 교정 여부를 결정한다.

        answer가 비어 있고 retry_count == 0이면 retry_prep_node → router_node 경로를 탄다.
        retry_prep_node가 retry_count를 1로 올리므로 다음 순회에서는 이 분기에 진입하지 않는다.
        재시도 여부 판단은 state["retry_count"]만으로 자기 완결된다.
        """
        retry_count = state.get("retry_count", 0)
        answer = state.get("answer") or ""

        needs_retry = not answer.strip() and retry_count == 0

        if needs_retry:
            return "retry_prep_node"
        return "end_normal"


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

        envelope = await get_cached_answer(
            refined,
            self._redis,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
        )
        if envelope is None:
            logger.info(
                "cache.miss room=%s intent=%s refined=%r",
                state.get("room_id"), intent.value, refined[:40],
            )
            return {"cache_hit": False}

        payload = envelope.get("payload", {}) or {}
        snap = envelope.get("state", {}) or {}
        logger.info(
            "cache.hit room=%s intent=%s refined=%r",
            state.get("room_id"), intent.value, refined[:40],
        )
        return {
            "answer": payload.get("answer"),
            "title": payload.get("title"),
            "vector_results": snap.get("vector_results"),
            "sql_results": snap.get("sql_results"),
            "max_class_name": snap.get("max_class_name"),
            "area_name": snap.get("area_name"),
            "service_status": snap.get("service_status"),
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

        payload = {
            "message_id": state.get("message_id"),
            "answer": answer,
            "intent": intent.value,
            "title": state.get("title"),
        }
        snap = {
            "refined_query": refined,
            "max_class_name": max_class_name,
            "area_name": area_name,
            "service_status": service_status,
            "vector_results": state.get("vector_results"),
            "sql_results": state.get("sql_results"),
        }
        await set_cached_answer(
            refined,
            payload,
            snap,
            self._redis,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
        )
        empty = not snap["vector_results"] and not snap["sql_results"]
        logger.info("cache.write intent=%s empty=%s", intent.value, empty)
        return {}
