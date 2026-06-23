"""관측 페이즈 — search_persist / trace 종단 노드 (best-effort) + trace 저장 헬퍼.

이 모듈의 logger 는 노드 패키지의 관측 경로 전반에서 patch 이음매로 쓰인다
(테스트 patch 타깃: agents.nodes.observability.logger).
"""

import json
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agents import _onai_gateway
from schemas.search import ChannelData, kind_of
from schemas.state import AgentState, IntentType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# search_persist INSERT SQL
# ---------------------------------------------------------------------------
# 두 상수는 ObservabilityNodes.search_persist_node 에서만 사용한다.
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


class ObservabilityNodes:
    """관측 페이즈 — search_persist / trace 종단 노드 (best-effort).

    의존 없음. INSERT SQL·_save_trace 는 모듈 전역 함수/상수를 호출한다.
    """

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

        세션 (노드 로컬, 0-6):
          ai_session 을 풀에서 잡아 두 테이블 INSERT 를 한 트랜잭션으로 커밋한 뒤 즉시
          반납한다. trace_node 는 별도 독립 세션을 연다 — search_persist 가 먼저 commit
          하므로 트랜잭션 공유 의존성이 없고, 한 노드의 INSERT/rollback 실패가 다른
          노드 세션을 오염시키지 않는다(관측 데이터 동시 유실 위험 제거).
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
            async with _onai_gateway.session() as ai_session:
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
            # 노드 로컬 세션은 async with 종료 시 자동 반납되므로 명시적 rollback 불필요.
            return {"node_path": ["search_persist_error"]}

    async def trace_node(self, state: AgentState) -> dict[str, Any]:
        """chat_agent_traces 저장 (best-effort 종단 노드).

        노드 로컬 세션(0-6): search_persist_node 와 독립된 ai_session 을 연다.
        """
        started_at = state.get("started_at") or time.monotonic()
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        # node_path: trace_node 자신은 아직 누적되지 않았으므로 state 의 누적분 + "trace".
        node_path = list(state.get("node_path") or []) + ["trace"]
        intent = state["plan"].get("intent")
        trace_payload: dict[str, Any] = {
            "intent": intent,
            "node_path": node_path,
            "elapsed_ms": elapsed_ms,
            "error": state.get("error"),
        }
        # ANALYTICS 관측치는 chat_search_results(service_id/score) 스키마에 맞지 않으므로
        # trace(JSONB) 확장으로 저장한다 (마이그레이션 없이).
        if intent == IntentType.ANALYTICS:
            analytics = state["analytics"]
            filters = state["filters"]
            analytics_rows = analytics.get("results") or []
            trace_payload["analytics"] = {
                "group_by": analytics.get("group_by"),
                "metric": analytics.get("metric"),
                "filters": {
                    "max_class_name": filters.get("max_class_name"),
                    "area_name": filters.get("area_name"),
                    "service_status": filters.get("service_status"),
                    "keyword": analytics.get("keyword"),
                },
                "result_count": len(analytics_rows),
                "result": analytics_rows,
            }
        try:
            async with _onai_gateway.session() as ai_session:
                await _save_trace(ai_session, state["message_id"], trace_payload)
        except Exception:
            # 세션 획득 실패도 best-effort 종단 노드 정책상 무시한다(워크플로우 결과 불변).
            logger.warning(
                "trace 세션 획득 실패 (message_id=%s)",
                state["message_id"],
                exc_info=True,
            )
        return {"trace": trace_payload, "node_path": ["trace"]}
