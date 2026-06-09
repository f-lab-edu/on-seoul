"""Triage Agent — action 결정 전담.

라우팅을 2단계로 책임 분리한 첫 단계다.
  - Triage(이 모듈): action 결정 (RETRIEVE/DIRECT_ANSWER/AMBIGUOUS/OUT_OF_SCOPE/EXPLAIN)
  - Router(agents.router_agent): action=RETRIEVE일 때 검색 계획(intent/refined_query/
    post-filter/secondary_intent) 수립

TriageAgent는 "어떤 검색 방식인지"·"어떤 필터인지"를 결정하지 않는다.
그 책임은 RETRIEVE로 판정된 뒤 RouterAgent가 담당한다.
"""

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agents.router_agent import build_context_block
from llm.client import get_chat_model
from llm.prompts.triage import TRIAGE_FEW_SHOT, TRIAGE_SYSTEM
from schemas.state import ActionType


class TriageOutput(BaseModel):
    """TriageAgent 구조화 출력 — action 결정 전용.

    검색 방식(intent)·필터·refined_query는 이 출력에 없다(RouterAgent 책임).
    out_of_scope_type은 OUT_OF_SCOPE action의 서브타입이며, attribute_gap 경로는
    그래프의 out_of_scope_node가 intent=VECTOR_SEARCH + vector_sub_intent=identification을
    고정 세팅하므로 여기서 검색 파라미터를 산출할 필요가 없다.
    """

    reasoning: str | None = Field(
        default=None,
        description="action 결정 근거 (CoT, 내부 전용)",
    )
    action: ActionType = Field(description="취할 행동 (5종)")
    out_of_scope_type: Literal["domain_outside", "attribute_gap"] | None = Field(
        default=None,
        description="action=OUT_OF_SCOPE일 때 서브타입",
    )
    user_rationale: str | None = Field(
        default=None,
        description="사용자에게 보여줄 판단 근거 1문장",
    )


class TriageAgent:
    """LCEL 기반 분류 에이전트 — action만 결정한다.

    history 컨텍스트 블록은 agents.router_agent.build_context_block을 재사용한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()

    async def classify(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        prev_reasoning: str | None = None,
    ) -> TriageOutput:
        """사용자 메시지를 분류하여 action을 결정한다.

        Args:
            message: 사용자 원본 발화.
            history: 직전 N턴 대화 이력(과거→최신).
            prev_reasoning: 직전 턴의 판단 근거. EXPLAIN action 가능 여부 판정에 사용.
        """
        context_block = build_context_block(history)
        system_parts = [TRIAGE_SYSTEM]
        if context_block:
            system_parts.append(context_block)
        # prev_reasoning 컨텍스트: EXPLAIN 판정을 위해 시스템 프롬프트에 추가.
        # 경계 마커로 감싸 역할 지시 삽입(prompt injection) 위험을 차단한다.
        if prev_reasoning:
            system_parts.append(
                "직전 답변의 판단 근거(prev_reasoning):\n"
                "---PREV_REASONING_START---\n"
                f"{prev_reasoning}\n"
                "---PREV_REASONING_END---"
            )
        system_text = "\n\n".join(system_parts)

        messages = [
            SystemMessage(content=system_text),
            *TRIAGE_FEW_SHOT.format_messages(),
            HumanMessage(content=f"<user_message>{message}</user_message>"),
        ]
        structured = self._llm.with_structured_output(TriageOutput)
        result: TriageOutput = await structured.ainvoke(messages)
        return result
