"""참조 경로 페이즈 — rehydrate / describe 노드 (DRILL·RELEVANCE).

참조 바인딩(인덱스→service_id)은 intake 페이즈(agents/nodes/intake.py)가 담당한다.
이 페이즈는 intake 가 바인딩한 target_service_ids 를 최신 원본으로 재-hydrate 하고
describe 로 설명하는 단계만 책임진다(규칙 기반 reference_resolution 은 제거됨).
"""

import logging
from typing import Any

from agents import _emit
from agents._ondata_gateway import OnDataReader, default_reader
from agents.answer_agent import AnswerAgent
from agents.detail_excerpt import is_content_question, prepare_detail_overview
from schemas.intake import TurnKind
from schemas.state import AgentState

logger = logging.getLogger(__name__)


class ReferenceNodes:
    """참조 경로 페이즈 — rehydrate / describe 노드.

    의존: answer(AnswerAgent.describe), ondata(OnDataReader — on_data 읽기 게이트웨이).

    B3-2(선택적 주입): rehydrate 의 on_data 읽기를 `OnDataReader` 생성자 주입으로 받는다.
    테스트는 가짜 OnDataReader 를 주입하거나 tool/세션 심볼(_hydrate_services/
    data_session_ctx)을 patch 해 격리한다. 기본값은 프로세스 공유 default_reader 다.
    """

    def __init__(
        self, answer: AnswerAgent, ondata: OnDataReader | None = None
    ) -> None:
        self._answer = answer
        self._ondata = ondata or default_reader

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
            rows = await self._ondata.hydrate(target_ids)
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

    async def _prepare_describe_overview(self, state: AgentState) -> str | None:
        """내용 질문 DRILL 의 focal detail_content fetch + 앞부분 발췌(best-effort).

        "어떤 프로그램/무슨 내용" 류(is_content_question)일 때만 focal service_id 의
        detail_content 를 fetch 해 상세내용 앞부분을 발췌한다. 그 외(속성 질문·RELEVANCE·
        focal 부재)는 즉시 None 을 반환해 fetch 를 태우지 않는다(지연 가드: 모든 DRILL 에
        DB read 를 붙이지 않는다). fetch/발췌 실패·빈약 → None(현행 describe 폴백).
        DB 는 on_data SELECT 전용.
        """
        if state["triage"].get("turn_kind") == TurnKind.RELEVANCE.value:
            return None
        if not is_content_question(state.get("message") or ""):
            return None
        rows = state["hydration"].get("hydrated_services") or []
        if not rows:
            return None
        focal_id = rows[0].get("service_id")
        if not focal_id:
            return None
        try:
            raw = await self._ondata.fetch_detail_content(focal_id)
            return prepare_detail_overview(raw)
        except Exception:
            logger.exception("describe overview 발췌 prep 실패 room=%s", state.get("room_id"))
            return None

    async def describe_node(self, state: AgentState) -> dict[str, Any]:
        """참조 해소 경로 — AnswerAgent.describe() 로 "어떤 곳인지" 서술.

        예약 카드 템플릿이 아니라 설명형 답변을 생성한다. 재-hydrate 0건이면
        AnswerAgent.describe 가 정직한 안내 + 재검색 제안을 반환한다.

        내용 질문("어떤 프로그램/무슨 내용")이면 focal detail_content 를 발췌해
        detail_excerpt 슬롯에 실어 describe 로 넘긴다(발췌 없으면 현행 서술로 폴백).
        """
        detail_excerpt = await self._prepare_describe_overview(state)
        describe_state = (
            state if detail_excerpt is None else {**state, "detail_excerpt": detail_excerpt}
        )
        try:
            new_state = await self._answer.describe(describe_state)
            return {
                "output": {
                    "answer": new_state.get("answer"),
                    "service_cards": new_state.get("service_cards"),
                },
                "detail_excerpt": detail_excerpt,
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
