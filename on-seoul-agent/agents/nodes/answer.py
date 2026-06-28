"""답변 페이즈 — action별 답변 노드 + answer_node."""

import logging
from typing import Any

from agents.answer_agent import _CLARIFY_FALLBACK, AnswerAgent
from agents.nodes._shared import _FALLBACK_ANSWER, is_gap_oos
from schemas.state import AgentState, IntentType

logger = logging.getLogger(__name__)


class AnswerNodes:
    """답변 페이즈 — action별 답변 노드 + answer_node.

    의존: answer(AnswerAgent). explain_node→direct_answer_node 교차 호출은
    동일 클래스 내부이므로 self.direct_answer_node 로 유지된다.
    """

    def __init__(self, answer: AnswerAgent) -> None:
        self._answer = answer

    async def direct_answer_node(self, state: AgentState) -> dict[str, Any]:
        """DIRECT_ANSWER action — DB 없이 LLM 직접 응답.

        기존 FALLBACK 안내문을 대체한다.
        반환 dict에 intent=FALLBACK을 명시적으로 세팅하여 AnswerAgent가 FALLBACK
        분기(대화형 프롬프트)를 타도록 보장한다. intake_node는 action만 채우고 intent를
        세팅하지 않으므로, 여기서 보장해야 DIRECT_ANSWER 직접 진입과 EXPLAIN 폴백
        (explain_node가 prev_reasoning 없을 때 이 노드로 위임) 두 경로 모두 카드형
        페르소나 오적용 없이 일관되게 FALLBACK 답변을 생성한다.

        intent를 답변 생성 *이전*에 주입해야 AnswerAgent.answer가 이를 읽으므로,
        state를 갱신한 사본을 만들어 self._answer.answer에 전달한다.
        """
        fallback_state = {
            **state,
            "plan": {**state.get("plan", {}), "intent": IntentType.FALLBACK},
        }
        try:
            new_state = await self._answer.answer(fallback_state)
            # S1 빈 답변 가드: LLM 성공 후에도 answer 가 None/빈 문자열이면 폴백 문구로
            # 대체한다(UI 빈 말풍선 방지). AnswerAgent 보장이 1차, 이 가드가 2차 방어막.
            answer = new_state.get("answer")
            if not (answer or "").strip():
                answer = _FALLBACK_ANSWER
            return {
                "plan": {"intent": IntentType.FALLBACK},
                "output": {
                    "answer": answer,
                    "service_cards": new_state.get("service_cards"),
                },
                "node_path": ["direct_answer_node"],
            }
        except Exception as exc:
            logger.exception("direct_answer_node 실행 오류")
            return {
                "error": str(exc),
                "output": {"answer": _FALLBACK_ANSWER},
                "node_path": ["direct_answer_error"],
            }

    async def ambiguous_node(self, state: AgentState) -> dict[str, Any]:
        """AMBIGUOUS action — 대화 맥락 기반 명확화 질문 1개를 LLM으로 생성.

        TriageAgent가 이미 AMBIGUOUS로 판정한 경우에만 도달하므로
        신뢰도 게이팅은 triage 단계에서 완료됐다.

        AnswerAgent.clarify() 가 history(state 내)·user_rationale 을 컨텍스트로
        삼아 되물음을 생성한다. clarify() 자체도 LLM 오류 시 고정 폴백으로 graceful
        degrade 하지만, 노드 차원에서도 예외를 잡아 폴백 답변 + ambiguous_error
        node_path 를 둔다(describe/direct_answer 패턴과 동일). 비-RETRIEVE 경로라
        self-correction 대상은 아니다.
        """
        logger.info("ambiguous_node room=%s", state.get("room_id"))
        try:
            new_state = await self._answer.clarify(state)
            # S1 빈 답변 가드: clarify() 내부 폴백이 1차이나, 노드 차원에서도
            # answer 가 None/빈 문자열이면 _CLARIFY_FALLBACK 으로 대체한다(2차 방어막).
            answer = new_state.get("answer")
            if not (answer or "").strip():
                answer = _CLARIFY_FALLBACK
            return {
                "output": {
                    "answer": answer,
                    "service_cards": new_state.get("service_cards"),
                },
                "node_path": ["ambiguous_node"],
            }
        except Exception as exc:
            logger.exception("ambiguous_node 실행 오류")
            return {
                "error": str(exc),
                # 폴백 문구는 AnswerAgent._CLARIFY_FALLBACK 단일 출처를 재사용한다(drift 방지).
                "output": {"answer": _CLARIFY_FALLBACK},
                "node_path": ["ambiguous_error"],
            }

    async def out_of_scope_node(self, state: AgentState) -> dict[str, Any]:
        """OUT_OF_SCOPE action — 서브타입 분기.

        domain_outside: 즉시 거절 메시지, 검색 없음, END로.
        attribute_gap / operational_detail: refined_query + vector_sub_intent=
            attribute_gap 으로 vector_node → answer 경로. 데이터-성격 갭 프레이밍,
            환각 금지. 두 서브타입은 P5 전까지 동형이다(아래 is_gap_oos).
        """
        oos_type = state["triage"].get("out_of_scope_type")
        if is_gap_oos(oos_type):
            # attribute_gap / operational_detail 은 시설 식별 검색이 필요하므로 vector_node
            # 로 넘긴다. intent=VECTOR_SEARCH를 명시해야 HydrationNode가 올바르게
            # hydrate한다(HydrationNode는 intent==VECTOR_SEARCH를 체크해 hydrated_services
            # 를 채운다).
            #
            # 결정 C: 정상 DETAIL("이 시설 자세히")과 동일 신호(identification)로
            # 위장하지 않고 전용 vector_sub_intent 를 전달한다. 검색 동작(식별 검색)은
            # 동일하지만(vector_node/hydration 은 intent 만 보고 동작), AnswerAgent 는
            # 이 값으로 전용 분기를 선택한다.
            #
            # P5 승격: operational_detail(폭염·휴무·주차·우천)은 식별 검색 경로(VECTOR)는
            # attribute_gap 과 공유하되 sub_intent 를 "operational_detail" 로 분리한다 —
            # pre_answer prep 이 적재한 detail_excerpt 가 있으면 answer 가 운영-상세 발췌
            # 실답변을 생성하고(사례 162-163 근본 해소), 없으면 attribute_gap interim
            # 리다이렉트로 정직 폴백한다. attribute_gap 자체는 현행 유지("attribute_gap").
            # 검색 routing(vector/0건 게이트/retry/종료)은 여전히 is_gap_oos 동형.
            sub_intent = (
                "operational_detail"
                if oos_type == "operational_detail"
                else "attribute_gap"
            )
            breadcrumb = (
                "out_of_scope_operational_detail"
                if oos_type == "operational_detail"
                else "out_of_scope_attribute_gap"
            )
            logger.info(
                "out_of_scope.gap room=%s oos=%s sub=%s refined=%r",
                state.get("room_id"),
                oos_type,
                sub_intent,
                (state["plan"].get("refined_query") or "")[:40],
            )
            return {
                "plan": {
                    "intent": IntentType.VECTOR_SEARCH,
                    "vector_sub_intent": sub_intent,
                },
                "node_path": [breadcrumb],
            }
        # domain_outside: 즉시 거절
        rationale = state["triage"].get("user_rationale")
        answer = (
            rationale
            or "죄송합니다, 해당 질문은 서울 공공서비스 예약 챗봇의 서비스 범위를 벗어납니다."
        )
        logger.info("out_of_scope.domain_outside room=%s", state.get("room_id"))
        return {
            "output": {"answer": answer},
            "node_path": ["out_of_scope_domain_outside"],
        }

    async def explain_node(self, state: AgentState) -> dict[str, Any]:
        """EXPLAIN action — API 가 실어준 맥락 전부로 직전 판단 근거를 설명한다(S2, 원칙 §0).

        explain() 이 history/entities/prev_reasoning 을 모두 소비하므로, 셋 중 하나라도
        있으면 설명이 가능하다. 폴백 조건을 "맥락이 전혀 없을 때"로 둬, 과도하게
        direct_answer 로 빠지지 않게 한다(이전엔 prev_reasoning 없으면 무조건 폴백).
        LLM 예외 시 기존 "일시적인 오류" 폴백 유지.
        """
        ws = state.get("prev_working_set") or {}
        has_context = bool(
            (ws.get("reasoning") or state.get("prev_reasoning"))
            or state.get("history")
            or (ws.get("entities") or state.get("prev_entities"))
        )
        if not has_context:
            logger.info(
                "explain_node.fallback room=%s (no context)",
                state.get("room_id"),
            )
            # 맥락이 전혀 없으면 설명할 근거가 없으므로 직접 답변 경로로 폴백
            return await self.direct_answer_node(state)

        try:
            # 단순 string 포맷팅 대신 LLM 으로 재서술 — 내부 기술 토큰 노출 차단(S2).
            new_state = await self._answer.explain(state)
            answer = new_state.get("answer")
            if not (answer or "").strip():
                answer = _FALLBACK_ANSWER
            logger.info("explain_node room=%s", state.get("room_id"))
            return {"output": {"answer": answer}, "node_path": ["explain_node"]}
        except Exception as exc:
            logger.exception("explain_node 실행 오류")
            return {
                "error": str(exc),
                "output": {"answer": _FALLBACK_ANSWER},
                "node_path": ["explain_error"],
            }

    async def answer_node(self, state: AgentState) -> dict[str, Any]:
        """AnswerAgent.answer() 호출 — answer, service_cards 설정.

        제목 생성은 독립 병렬 노드(generate_title_node)로 분리됐다.
        """
        if state.get("error") and state["output"].get("answer"):
            return {"node_path": ["answer_node"]}

        try:
            new_state = await self._answer.answer(state)
            answer = new_state.get("answer") or ""
            logger.info(
                "answer.generated room=%s len=%d", state.get("room_id"), len(answer)
            )
            # 관측: 검색 결과는 있는데 카드가 비어 있으면 normalize 무음 실패 신호.
            # 동작은 바꾸지 않고 경고만 남긴다.
            intent = state["plan"].get("intent")
            if intent in (IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH):
                hydrated = state["hydration"].get("hydrated_services") or []
                sql_results = state["sql"].get("results") or []
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
                "output": {
                    "answer": new_state.get("answer"),
                    "service_cards": new_state.get("service_cards"),
                },
                "node_path": ["answer_node"],
            }
        except Exception as exc:
            logger.exception("answer_node 실행 오류")
            return {
                "error": str(exc),
                "output": {
                    "answer": "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
                },
                "node_path": ["answer_error"],
            }
