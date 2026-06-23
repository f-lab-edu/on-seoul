"""참조 해소 페이즈 — reference_resolution / rehydrate / describe 노드."""

import logging
from typing import Any

from agents import _emit, _ondata_gateway
from agents._reference_resolution import resolve_reference
from agents.answer_agent import AnswerAgent
from schemas.state import AgentState

logger = logging.getLogger(__name__)


class ReferenceNodes:
    """참조 해소 페이즈 — reference_resolution / rehydrate / describe 노드.

    의존: answer(AnswerAgent.describe).
    """

    def __init__(self, answer: AnswerAgent) -> None:
        self._answer = answer

    async def reference_resolution_node(self, state: AgentState) -> dict[str, Any]:
        """참조 해소 게이트 — START 직후 선판정.

        현재 message 가 직전 턴 결과 엔티티를 가리키는 "지시 참조"인지 규칙 기반으로
        판정한다(LLM 미사용 — 결정적·저지연·무비용). prev_entities 가 비어 있으면
        무조건 non-referential 이므로 기존 흐름과 100% 하위호환된다.

        referential → target_service_ids 바인딩(서수/라벨/지시어, 다중 가능).
                      route_after_reference 엣지가 search 경로를 우회한다.
        non-referential → target_service_ids=None, router_node 로 진행(기존 흐름).
        """
        prev_entities = state.get("prev_entities") or []
        target_ids = resolve_reference(state["message"], prev_entities)
        if target_ids:
            logger.info(
                "reference.resolved room=%s targets=%s",
                state.get("room_id"),
                target_ids,
            )
            return {
                "target_service_ids": target_ids,
                "node_path": ["reference_resolution"],
            }
        return {
            "target_service_ids": None,
            "node_path": ["reference_resolution"],
        }

    async def rehydrate_node(self, state: AgentState) -> dict[str, Any]:
        """참조 해소 경로 — target_service_ids 의 최신 원본을 재-hydrate.

        스냅샷 캐싱 금지(staleness 위험): 정체성(service_id)만 이어받고 사실(상태·
        일정)은 hydrate_services 로 최신 원본에서 재조회한다. 노드 로컬 data_session
        (0-6)으로 풀에서 잡고 조회 후 즉시 반납한다.

        재-hydrate 0건(soft-delete/마감)은 hydrated_services=[] 로 두고, describe_node
        가 정직한 안내 + 재검색 제안을 답한다(환각·빈 카드 금지).
        """
        target_ids = state.get("target_service_ids") or []
        # 참조 해소 경로: 재-hydrate 후 describe 답변 단계로 — answering emit.
        guard = _emit.emit_answering(state)
        try:
            rows = await _ondata_gateway.hydrate(target_ids)
            logger.info(
                "rehydrate.done room=%s requested=%d hydrated=%d",
                state.get("room_id"),
                len(target_ids),
                len(rows),
            )
            return {
                "hydration": {"hydrated_services": rows},
                "node_path": ["rehydrate_node"],
                **guard,
            }
        except Exception:
            logger.exception("rehydrate_node 실행 오류")
            return {
                "hydration": {"hydrated_services": []},
                "node_path": ["rehydrate_error"],
                **guard,
            }

    async def describe_node(self, state: AgentState) -> dict[str, Any]:
        """참조 해소 경로 — AnswerAgent.describe() 로 "어떤 곳인지" 서술.

        예약 카드 템플릿이 아니라 설명형 답변을 생성한다. 재-hydrate 0건이면
        AnswerAgent.describe 가 정직한 안내 + 재검색 제안을 반환한다.
        """
        try:
            new_state = await self._answer.describe(state)
            return {
                "output": {
                    "answer": new_state.get("answer"),
                    "service_cards": new_state.get("service_cards"),
                },
                "node_path": ["describe_node"],
            }
        except Exception as exc:
            logger.exception("describe_node 실행 오류")
            return {
                "error": str(exc),
                "output": {
                    "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                },
                "node_path": ["describe_error"],
            }

    def route_after_reference(self, state: AgentState) -> str:
        """reference_resolution_node 직후 라우팅.

        referential(target_service_ids 채워짐) → rehydrate_node(검색 우회).
        non-referential → triage_node(기존 흐름).
        """
        if state.get("target_service_ids"):
            return "rehydrate_node"
        return "triage_node"
