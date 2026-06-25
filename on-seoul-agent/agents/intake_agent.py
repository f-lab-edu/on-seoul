"""Intake Agent — reference_resolution + triage 병합 (단일 LLM 분류).

턴 1회 with_structured_output 호출로 turn_kind + action + ref_indices + oos_type 을
한 번에 판정한다. prev_entities 를 1..N 열거(라벨 포함, ≤10)해 LLM 에 제시하고,
LLM 은 인덱스만 선택한다(service_id 생성 금지 — agents/_intake_indexing.py 가 매핑).

SQL/DB 는 손대지 않는다(원칙 유지). refined_query·필터는 RETRIEVE 시 router_node 가
계속 담당한다(분리 유지 — intake 는 *분류*까지만).
"""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents._intake_indexing import enumerate_entities
from agents.router_agent import build_context_block
from llm.client import get_chat_model
from llm.prompts.intake import INTAKE_FEW_SHOT, INTAKE_SYSTEM
from schemas.intake import IntakeOutput


class IntakeAgent:
    """LCEL 기반 입구 분류 에이전트 — turn_kind + action + ref_indices 한 번에 판정.

    history 컨텍스트 블록은 agents.router_agent.build_context_block 을 재사용한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()

    async def classify(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        prev_entities: list[dict[str, Any]] | None = None,
        prev_reasoning: str | None = None,
    ) -> IntakeOutput:
        """사용자 메시지를 분류한다(turn_kind/action/ref_indices/oos_type).

        Args:
            message: 사용자 원본 발화.
            history: 직전 N턴 대화 이력(과거→최신).
            prev_entities: 직전 턴 결과 엔티티 [{service_id, label}, ...]. 1..N 열거됨.
            prev_reasoning: 직전 판단 근거. META 판정에 사용.
        """
        system_parts = [INTAKE_SYSTEM]
        context_block = build_context_block(history)
        if context_block:
            system_parts.append(context_block)

        # prev_entities 열거(인덱스 계약) — LLM 이 인덱스를 고를 목록.
        enumerated = enumerate_entities(prev_entities)
        if enumerated:
            system_parts.append(
                "직전 결과(prev_entities) — 1-based 인덱스로 가리키세요:\n"
                "---PREV_ENTITIES_START---\n"
                f"{enumerated}\n"
                "---PREV_ENTITIES_END---"
            )

        # prev_reasoning 컨텍스트(META 판정). 경계 마커로 injection 차단.
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
            *INTAKE_FEW_SHOT.format_messages(),
            HumanMessage(content=f"<user_message>{message}</user_message>"),
        ]
        structured = self._llm.with_structured_output(IntakeOutput)
        result: IntakeOutput = await structured.ainvoke(messages)
        return result
