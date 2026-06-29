"""Answer Agent — 자연어 답변 + 시설 카드 가공.

AgentState의 검색 결과(sql_results / vector_results / map_results)를 종합해
사용자에게 전달할 최종 답변과 시설 카드 목록을 생성한다.

대화 제목 생성은 독립 병렬 노드(agents/nodes/title.py:generate_title_node)가
별도 SSE 이벤트로 담당한다 — answer 경로는 더 이상 title 을 다루지 않는다.

## 프롬프트 조립 구조 (2-Tier)

Tier 1 — __init__ 1회 조립 (MAP / ANALYTICS / FALLBACK):
  조건부 절이 없으므로 self._static_prompts dict에 완전 캐시.

Tier 2 — 런타임 조립 (SQL_SEARCH / VECTOR_SEARCH):
  _build_card_system(message, results) 가 호출마다 조건부 절을 평가하여 조립.
  조건: "접수중" 시설 존재 여부, 사용자 질문 내 자치구 명시 여부.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from agents._intake_indexing import enumerate_entities
from agents.answer import cards, prompting
from agents.answer.cards import _DISPLAY_LIMIT
from agents.router_agent import build_context_block
from llm.client import get_chat_model
from llm.prompts.answer import (
    _ANSWER_HUMAN,
    _CLARIFY_FALLBACK,
    _FALLBACK_GUARDRAILS,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_ANALYTICS,
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_CLARIFY,
    _STRUCT_DESCRIBE,
    _STRUCT_DESCRIBE_EMPTY,
    _STRUCT_DETAIL,
    _STRUCT_EXPLAIN,
    _STRUCT_FALLBACK,
    _STRUCT_MAP,
    _STRUCT_OPERATIONAL_DETAIL,
    _STRUCT_RELEVANCE,
)
from schemas.intake import TurnKind
from schemas.state import AgentState, IntentType


class AnswerAgent:
    """검색 결과 → 자연어 답변 + 시설 카드 생성 에이전트.

    ## 프롬프트 조립 전략

    __init__에서 MAP/ANALYTICS/FALLBACK 시스템 프롬프트를 self._static_prompts에
    캐시한다(Tier 1). SQL_SEARCH/VECTOR_SEARCH는 answer() 호출 시 _build_card_system이
    조건부 절을 평가하여 조립한다(Tier 2).

    _answer_chain은 단일 체인으로 유지하되, system 메시지를 {system} 변수로
    파라미터화하여 intent별 분기를 answer() 내에서 처리한다. 이 방식은 기존
    단위 테스트가 agent._answer_chain.ainvoke를 mock하는 구조와 완전 호환된다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()

        # system 메시지를 {system} 변수로 파라미터화: intent별 프롬프트를 runtime에 주입.
        # human 메시지는 {message}/{results_json}/{more_notice} 변수를 사용한다.
        # {more_notice}는 _more_notice(extra_count)로 코드에서 생성해 주입한다
        # (렌더 가능한 숫자 0을 LLM에 노출하지 않기 위함).
        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{system}"),
                ("human", _ANSWER_HUMAN),
            ]
        )
        self._answer_chain = answer_prompt | llm | StrOutputParser()

        # Tier 1: 조건부 절 없는 intent 시스템 프롬프트를 init 1회 조립 후 캐시.
        self._static_prompts: dict[str, str] = {
            IntentType.MAP.value: prompting._compose(_ROLE, _STRUCT_MAP, _OUTPUT_RULES),
            IntentType.ANALYTICS.value: prompting._compose(
                _ROLE, _STRUCT_ANALYTICS, _OUTPUT_RULES
            ),
            # FALLBACK 은 가드레일 블록을 추가로 끼워 조립한다(공격 표면 방어).
            IntentType.FALLBACK.value: prompting._compose(
                _ROLE, _STRUCT_FALLBACK, _FALLBACK_GUARDRAILS, _OUTPUT_RULES
            ),
            # 단일 엔티티 상세형 (VECTOR_SEARCH + identification). 조건부 절 없음.
            "DETAIL": prompting._compose(_ROLE, _STRUCT_DETAIL, _OUTPUT_RULES),
            # attribute_gap 전용 (OUT_OF_SCOPE/attribute_gap). identification 과 분리.
            "ATTRIBUTE_GAP": prompting._compose(
                _ROLE, _STRUCT_ATTRIBUTE_GAP, _OUTPUT_RULES
            ),
            # operational_detail 운영-상세 발췌형. detail_excerpt 존재 시 전용 분기.
            "OPERATIONAL_DETAIL": prompting._compose(
                _ROLE, _STRUCT_OPERATIONAL_DETAIL, _OUTPUT_RULES
            ),
            # describe-known-entity (참조 해소 경로). intent 와 무관한 전용 키.
            "DESCRIBE": prompting._compose(_ROLE, _STRUCT_DESCRIBE, _OUTPUT_RULES),
            # describe-relevance (RELEVANCE turn_kind). 적합성 설명 변형.
            "RELEVANCE": prompting._compose(_ROLE, _STRUCT_RELEVANCE, _OUTPUT_RULES),
            "DESCRIBE_EMPTY": prompting._compose(
                _ROLE, _STRUCT_DESCRIBE_EMPTY, _OUTPUT_RULES
            ),
            # AMBIGUOUS 명확화 — history/user_rationale 는 clarify() 런타임에 주입한다.
            # CLARIFY 는 FALLBACK 과 동일 위협 모델(임의 발화 + history.content + unescaped
            # {message} 가 되물음에 반향될 표면)이므로 _FALLBACK_GUARDRAILS 를 끼워
            # 역할 주입·내부정보 유출·지시 반향을 차단한다. 출력은 여전히 "되물음 1문장".
            "CLARIFY": prompting._compose(_ROLE, _STRUCT_CLARIFY, _FALLBACK_GUARDRAILS),
            # EXPLAIN 재서술 — 기술 토큰 노출 차단 가드레일을 함께 끼운다(임의 토큰 반향 표면).
            "EXPLAIN": prompting._compose(_ROLE, _STRUCT_EXPLAIN, _FALLBACK_GUARDRAILS),
        }

    async def explain(self, state: AgentState) -> AgentState:
        """EXPLAIN 경로 — 직전 판단의 근거를, API 가 실어준 맥락 전부로 설명한다.

        clarify() 와 동일한 런타임 합성 패턴을 따른다 — 실제 사용자 질문은 human
        message 자리에 그대로 전달하고(무엇에 대한 "왜"인지 LLM 이 인지), 맥락은
        system 에 경계 마커로 감싸 주입한다:
        - history(build_context_block): 사용자가 가리키는 직전 판단을 찾을 1차 근거.
          단일 슬롯 prev_reasoning 이 직전 턴만 운반하는 한계를 history 가 보완한다.
        - 운반된 entities(prev_working_set.entities 우선, 없으면 prev_entities):
          직전에 안내된 시설을 근거로.
        - prev_reasoning(보조): 직전 턴 분류 근거 1문장.

        history/entities/reasoning 은 모두 클라이언트가 운반한 값이라 임의 발화·역할
        주입이 섞일 수 있으므로 각각 경계 마커로 감싸고, _STRUCT_EXPLAIN +
        _FALLBACK_GUARDRAILS 가 마커 안 텍스트를 설명 근거 데이터로만 취급하도록
        강제한다. 카드 없음(service_cards=[]).
        """
        message = state["message"]
        system_parts = [self._static_prompts["EXPLAIN"]]

        # history — 사용자가 가리키는 직전 판단(예: 데이트 검색)을 찾을 1차 근거.
        context_block = build_context_block(state.get("history"))
        if context_block:
            system_parts.append(
                "직전 대화 이력(설명 근거 데이터):\n"
                "---HISTORY_START---\n"
                f"{context_block}\n"
                "---HISTORY_END---"
            )

        # 운반된 entities — 신규 채널(prev_working_set) 우선, 평면 슬롯 폴백.
        ws = state.get("prev_working_set") or {}
        entities = ws.get("entities") or state.get("prev_entities")
        enumerated = enumerate_entities(entities)
        if enumerated:
            system_parts.append(
                "직전에 안내된 시설(설명 근거 데이터):\n"
                "---ENTITIES_START---\n"
                f"{enumerated}\n"
                "---ENTITIES_END---"
            )

        # prev_reasoning — 보조 맥락(직전 턴 분류 근거). 신규 채널 우선.
        prev_reasoning = ws.get("reasoning") or state.get("prev_reasoning")
        if prev_reasoning:
            system_parts.append(
                "직전 턴 판단 근거(보조, 설명 근거 데이터):\n"
                "---REASONING_START---\n"
                f"{prev_reasoning}\n"
                "---REASONING_END---"
            )

        system_prompt = prompting._compose(*system_parts)
        answer_text = await self._answer_chain.ainvoke(
            {
                "system": system_prompt,
                "message": message,
                "results_json": "[]",
                "more_notice": prompting._more_notice(0),
            }
        )
        return {**state, "answer": answer_text, "service_cards": []}

    async def describe(self, state: AgentState) -> AgentState:
        """참조 해소 경로 — 재-hydrate 한 엔티티를 turn_kind 에 따라 서술한다.

        turn_kind 분기:
        - RELEVANCE(집합 적합성, "왜 이 항목들이 {성격}이야?") → _STRUCT_RELEVANCE 로
          "왜 이게 맞는지"를 결과 속성으로 묶어 설명한다(사례 156: 현재형 no-results
          변질 차단). 원 성격 키워드는 human 템플릿의 message 로 전달된다.
        - DRILL/기타(개별 상세, "어떤 곳이야?") → 현행 _STRUCT_DESCRIBE 유지.

        hydrated_services 가 비어 있으면(재-hydrate 0건: soft-delete/마감) turn_kind 와
        무관하게 정직한 안내 + 재검색 제안만 답한다(환각·빈 카드 금지).
        """
        message = state["message"]
        hydrated = state["hydration"].get("hydrated_services") or []

        if not hydrated:
            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": self._static_prompts["DESCRIBE_EMPTY"],
                    "message": message,
                    "results_json": "[]",
                    "more_notice": prompting._more_notice(0),
                }
            )
            # 0건은 카드 노출 없음.
            return {**state, "answer": answer_text, "service_cards": []}

        # 참조 집합 설명(DRILL/RELEVANCE)은 검색-쏠림 자각(result_quality) 비대상이다 —
        # 사용자가 명시적으로 참조한 집합이라 "특정 지역에 쏠려 있다" 류 캐비엇이 부적절
        # (자각 패스는 RETRIEVE 전용이라 pre_answer_gate 가 이 경로엔 result_quality 를
        # 산출하지 않음). 의도된 경계이므로 describe 는 result_quality 를 읽지 않는다.
        # RELEVANCE(집합 적합성)면 적합성 설명 변형, 그 외(DRILL 포함)는 현행 describe.
        turn_kind = state["triage"].get("turn_kind")
        prompt_key = (
            "RELEVANCE" if turn_kind == TurnKind.RELEVANCE.value else "DESCRIBE"
        )

        display = [self._normalize(r) for r in hydrated[:_DISPLAY_LIMIT]]
        results_json = json.dumps(display, ensure_ascii=False, default=str)
        answer_text = await self._answer_chain.ainvoke(
            {
                "system": self._static_prompts[prompt_key],
                "message": message,
                "results_json": results_json,
                "more_notice": prompting._more_notice(0),
            }
        )
        return {
            **state,
            "answer": answer_text,
            "service_cards": [dict(card) for card in display],
        }

    async def clarify(self, state: AgentState) -> AgentState:
        """AMBIGUOUS 경로 — 대화 맥락을 반영한 명확화 질문 1개를 생성한다.

        history(직전 N턴)를 build_context_block 으로 변환해 system 컨텍스트에 주입한다
        (triage/router/describe 와 동일 헬퍼 재사용 — 일관성·injection 경계 유지).
        user_rationale 이 있으면 힌트로 경계 마커에 감싸 system 에 포함한다(역할 지시
        삽입 차단). 추측 답변이 아니라 무엇을 좁힐지 되묻는 한 문장을 생성한다.

        LLM 오류/빈 출력 시 고정 폴백 안내문으로 graceful fallback 하여 사용자 응답이
        절대 비지 않도록 한다. 명확화는 카드가 없으므로 service_cards=[] 를 반환한다.
        """
        message = state["message"]
        system_parts = [self._static_prompts["CLARIFY"]]

        context_block = build_context_block(state.get("history"))
        if context_block:
            system_parts.append(context_block)

        # user_rationale: triage 가 산출한 모호 근거 힌트. 경계 마커로 감싸 주입한다.
        rationale = state["triage"].get("user_rationale")
        if rationale:
            system_parts.append(
                "참고용 모호성 힌트(user_rationale):\n"
                "---RATIONALE_START---\n"
                f"{rationale}\n"
                "---RATIONALE_END---"
            )

        system_prompt = prompting._compose(*system_parts)
        try:
            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": "[]",
                    "more_notice": prompting._more_notice(0),
                }
            )
        except Exception:
            answer_text = ""
        if not (answer_text or "").strip():
            answer_text = _CLARIFY_FALLBACK
        return {**state, "answer": answer_text, "service_cards": []}

    async def answer(self, state: AgentState) -> AgentState:
        """검색 결과를 종합해 answer 를 채운 AgentState를 반환한다.

        intent별 분기:
        - ANALYTICS: analytics_results를 직접 읽어 LLM에 전달. service_cards=[].
        - FALLBACK:  빈 JSON 배열 전달. service_cards=[].
        - MAP:       _collect_results 경로(GeoJSON features 언팩). service_cards 기존 경로.
        - SQL_SEARCH / VECTOR_SEARCH / None: _build_card_system으로 Tier 2 조립.
          상위 _DISPLAY_LIMIT건 슬라이스 + extra_count.
        """
        intent = state["plan"].get("intent")
        message = state["message"]

        if intent == IntentType.ANALYTICS:
            # ANALYTICS: analytics 결과를 직접 LLM에 전달. _normalize 미경유.
            # 카드 미표시 개념이 없으므로 _more_notice(0)('외 N건' 금지 문구)을 주입한다.
            system_prompt = self._static_prompts[IntentType.ANALYTICS.value]
            raw_analytics = state["analytics"].get("results") or []
            results_json = json.dumps(raw_analytics, ensure_ascii=False, default=str)
            answer_text: str = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": results_json,
                    "more_notice": prompting._more_notice(0),
                }
            )
            updates: dict = {"answer": answer_text, "service_cards": []}

        elif intent == IntentType.FALLBACK:
            system_prompt = self._static_prompts[IntentType.FALLBACK.value]
            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": "[]",
                    "more_notice": prompting._more_notice(0),
                }
            )
            updates = {"answer": answer_text, "service_cards": []}

        else:
            # MAP, SQL_SEARCH, VECTOR_SEARCH, None
            all_results = self._collect_results(state)

            sub_intent = state["plan"].get("vector_sub_intent")

            # operational_detail 운영-상세 발췌형 트리거: out_of_scope_node 가 세팅한
            # vector_sub_intent=="operational_detail" + pre_answer prep 이 적재한
            # detail_excerpt 존재 시. detail_excerpt 가 None 이면(키워드 부재·raw 없음 등)
            # attribute_gap interim 리다이렉트로 폴백한다(정직 "공식 페이지 확인").
            detail_excerpt = state.get("detail_excerpt")
            is_operational_detail = (
                intent == IntentType.VECTOR_SEARCH
                and sub_intent == "operational_detail"
                and bool(detail_excerpt)
            )

            # attribute_gap 전용 트리거 — is_attribute_gap 과 is_detail 은 상호배타다.
            # out_of_scope_node 가 세팅한
            # vector_sub_intent=="attribute_gap" 신호로 DETAIL(identification)과 분리한다.
            # 검색은 동일하게 식별 검색을 수행했으므로 focal 시설을 앞으로 끌어올리되,
            # 답변은 데이터-성격 갭 프레이밍 프롬프트로 생성한다(예약 정보만 풀로
            # 나열하던 결함 차단). 결과 유무는 프롬프트 내부에서 분기한다(빈 배열도 허용).
            # operational_detail 이지만 detail_excerpt 가 None 이면 여기로 폴백한다.
            is_attribute_gap = (
                intent == IntentType.VECTOR_SEARCH
                and sub_intent in ("attribute_gap", "operational_detail")
                and not is_operational_detail
            )

            # 단일 엔티티 상세형 트리거: VECTOR_SEARCH + vector_sub_intent=identification.
            # focal(첫=RRF 최상위) place_name 공간들을 앞으로 끌어올려 _DISPLAY_LIMIT
            # 슬라이스에서 잘리지 않게 한다(C). 그 외 intent/sub_intent 는 현행 유지.
            # 트리거는 vector_sub_intent == "identification" 정확 일치다.
            # 라우터가 실제 산출하는 "detail" 값에는 의도적으로 발동하지 않는다
            # — identification(단일 시설 지목) 만 상세형, "detail" 은 목록형 유지.
            is_detail = (
                intent == IntentType.VECTOR_SEARCH
                and sub_intent == "identification"
                and bool(all_results)
            )
            # attribute_gap / operational_detail 도 식별 검색이므로 focal 우선 배치를
            # 공유한다(추정 시설을 슬라이스 상단에 둔다).
            if (is_detail or is_attribute_gap or is_operational_detail) and all_results:
                all_results = cards._focal_first(all_results)

            # 카드형(Tier 2) 턴은 pre_answer_gate 가 결정적으로 큐레이션한 display/
            # extra_count 를 그대로 렌더링한다(큐레이션 상류화). answer 는 슬라이스/
            # extra_count 계산을 하지 않는다(생성 전용). 상세형/attribute_gap/MAP 은
            # 큐레이션 비대상이라 슬롯이 None → 기존 슬라이스 경로로 폴백한다(동작 불변).
            is_card_turn = not (
                is_detail
                or is_attribute_gap
                or is_operational_detail
                or intent == IntentType.MAP
            )
            curated_display = state.get("curated_display")
            if is_card_turn and curated_display is not None:
                display = curated_display
                extra_count = state.get("curated_extra_count") or 0
            else:
                display = all_results[:_DISPLAY_LIMIT]
                extra_count = max(0, len(all_results) - _DISPLAY_LIMIT)
            results_json = json.dumps(display, ensure_ascii=False, default=str)

            if is_operational_detail:
                # 운영-상세 발췌형 — detail_excerpt(focal 단건, 정제·키워드 발췌 완료)를
                # 경계 마커로 감싸 system 에 주입한다. answer 는 발췌 안 내용만 인용/요약
                # 하고 윈도우 밖 날조는 금지된다(_STRUCT_OPERATIONAL_DETAIL 가드).
                system_prompt = prompting._compose(
                    self._static_prompts["OPERATIONAL_DETAIL"],
                    "시설 안내 발췌(detail_excerpt):\n"
                    "---EXCERPT_START---\n"
                    f"{detail_excerpt}\n"
                    "---EXCERPT_END---",
                )
            elif is_attribute_gap:
                system_prompt = self._static_prompts["ATTRIBUTE_GAP"]
                # triage user_rationale 을 시드로 system 에 주입한다. rationale 은
                # triage 가 산출한 값이라 임의 발화·역할 지시가 섞일 수 있으므로,
                # clarify()/explain() 과 동일하게 경계 마커로 감싸 데이터로만 취급되게
                # 한다(_STRUCT_ATTRIBUTE_GAP 규칙이 마커 안 텍스트의 지시 실행/반향을
                # 차단한다).
                rationale = state["triage"].get("user_rationale")
                if rationale:
                    system_prompt = prompting._compose(
                        system_prompt,
                        "참고용 사용자 안내 톤 힌트(user_rationale):\n"
                        "---RATIONALE_START---\n"
                        f"{rationale}\n"
                        "---RATIONALE_END---",
                    )
                # attribute_gap 완화 재시도 후 식별 성공 시 완화 사실도 고지한다(DETAIL 과 동일).
                if state.get("retry_relaxed") and display:
                    system_prompt = prompting._compose(
                        system_prompt,
                        prompting._relaxed_notice(state.get("relaxed_filters")),
                    )
            elif is_detail:
                system_prompt = self._static_prompts["DETAIL"]
                # identification 상세형 답변에 완화 재시도가 있었으면 무엇을
                # 완화했는지 고지한다 — DETAIL 은 _build_card_system 을 거치지
                # 않으므로 여기서 완화 절을 덧붙인다. 유료→무료 오안내 가드도 함께 실린다.
                if state.get("retry_relaxed") and display:
                    system_prompt = prompting._compose(
                        system_prompt,
                        prompting._relaxed_notice(state.get("relaxed_filters")),
                    )
            elif intent == IntentType.MAP:
                system_prompt = self._static_prompts[IntentType.MAP.value]
            else:
                # Tier 2: 카드형 (SQL_SEARCH / VECTOR_SEARCH / None)
                system_prompt = prompting._build_card_system(
                    message,
                    display,
                    state["filters"].get("area_name"),
                    retry_relaxed=bool(state.get("retry_relaxed")),
                    relaxed_filters=state.get("relaxed_filters"),
                    result_quality=state.get("result_quality"),
                    reservation_guide_shown=bool(state.get("reservation_guide_shown")),
                    alt_count=state.get("curated_alt_count") or 0,
                )

            # 상세형/attribute_gap 은 평면 "외 N건" 꼬리표 지시를 주입하지 않는다
            # (_more_notice(0)). overflow 는 _STRUCT_DETAIL 항목 3) 보조 목록이 직접
            # 처리하고, attribute_gap 은 목록 나열형이 아니라 갭 안내형이라 꼬리표가
            # 무의미하므로 중립화한다.
            notice = (
                prompting._more_notice(0)
                if (is_detail or is_attribute_gap or is_operational_detail)
                else prompting._more_notice(extra_count)
            )
            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": results_json,
                    "more_notice": notice,
                }
            )

            # service_cards 슬롯에는 shallow copy 로 분리한다.
            # display 리스트는 LLM 입력(results_json) 직렬화에 이미 사용된 동일 참조이며,
            # 향후 LLM 전처리 단계가 추가되어 inplace mutate 될 경우 외부 노출 경로
            # (SSE final payload, cache envelope) 가 오염될 수 있다. 최대 5건 × 12 필드라
            # 복사 비용은 무시 가능.
            updates = {
                "answer": answer_text,
                "service_cards": [dict(card) for card in display],
            }

        return {**state, **updates}

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _collect_results(self, state: AgentState) -> list[dict]:
        """검색 결과를 단일 목록으로 합친다.

        우선순위:
          1. hydrated_services  — HydrationNode 가 채운 통합 슬롯 (정식 경로)
          2. sql_results / vector_results — 통합 슬롯 미설정 시 호환 폴백
             (cache hit envelope 또는 단위 테스트에서 HydrationNode 없이 호출되는 경우)
          3. map_results        — GeoJSON 구조라 별도로 unpack

        ANALYTICS 결과(analytics_results)는 여기서 처리하지 않는다.
        집계 행은 _normalize가 맞지 않으므로 answer()에서 직접 처리한다.

        HydrationNode 가 그래프에 정상 삽입된 정식 경로에서는 항상 (1)로 처리된다.
        """
        raw: list[dict] = []

        hydrated = state["hydration"].get("hydrated_services")
        if hydrated is not None:
            raw.extend(hydrated)
        else:
            # 폴백 — hydrated_services 슬롯이 비었을 때만 검색 경로별 슬롯에서 채집.
            sql_results = state["sql"].get("results")
            vector_results = state["vector"].get("results")
            if sql_results:
                raw.extend(sql_results)
            if vector_results:
                raw.extend(vector_results)

        map_results = state["map"].get("results")
        if map_results:
            # map 결과는 GeoJSON dict — features 배열 언팩
            features = map_results.get("features", [])
            raw.extend(f.get("properties", {}) for f in features)

        return [self._normalize(r) for r in raw]

    @staticmethod
    def _normalize(row: dict) -> dict:
        """카드 렌더링에 필요한 필드를 추출하고 fallback URL을 보정한다.

        sql_results와 vector_results는 모두 public_service_reservations 원본 컬럼을
        평탄 dict로 가지므로 metadata 언팩 분기는 더 이상 필요하지 않다.
        map_results는 GeoJSON Feature의 properties dict를 그대로 받는다.

        프롬프트에서 실제로 출력하는 필드만 LLM 컨텍스트에 노출한다.

        ## 답변 가능 속성 카탈로그

        카드/LLM 컨텍스트에 노출하는 필드 = 카드 필드 + hydration 이 끌어오는 보유
        정형 컬럼. use_time_start/end(이용시간)·cancel_std_type/days(취소기준)·
        tel_no(문의처)를 편입한다. 이 컬럼들은 TIME/SMALLINT/VARCHAR 로, 수집 단계
        (DateUtil.parseTime 등)에서 malformed 값을 null 로 거르고 24h 초과를 정규화하는
        방어 변환을 거치므로 service_open_*_dt 와 달리 신뢰 가능하다. 없는 값은 None 으로
        통과되어 프롬프트에서 자연히 생략된다(날조 금지 유지).

        ## 의도적 제외 필드: service_open_start_dt / service_open_end_dt (운영 기간)

        DB(`public_service_reservations`) 의 운영 기간 컬럼에 신뢰할 수 없는 값이
        다수 존재한다 (예: 2021-01-01 ~ 2031-12-30 처럼 10년에 걸친 비현실적 범위).
        사용자가 답변에서 이 값을 보면 혼란을 유발하므로 LLM 컨텍스트에서 아예
        제외한다. 결과적으로:
          - `_normalize()` 반환 dict 에 두 필드를 **포함하지 않는다** (현재 구현).
          - 데이터 신뢰성이 개선되면(별도 작업) 다시 노출 검토.

        extractor 메타데이터(fee/operating_hours/cancellation 등)는 임베딩 전용이라
        여기서 조인하지 않는다.

        구현은 모듈 레벨 _normalize_card_row 로 위임한다(pre_answer_gate 큐레이션과
        동일 정규화 공유 — 단일 출처).
        """
        return cards._normalize_card_row(row)
