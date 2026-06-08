"""Triage Agent — action 결정 + retrieval_intent 분류.

RouterAgent의 2축 확장:
  - action 축: RETRIEVE | DIRECT_ANSWER | AMBIGUOUS | OUT_OF_SCOPE | EXPLAIN
  - retrieval_intent 축: primary_intent (SQL_SEARCH / VECTOR_SEARCH / MAP / ANALYTICS)

기존 RouterAgent(_IntentOutput)와 완전 하위호환:
  - action=RETRIEVE일 때 `intent` 필드 = primary_intent (기존 코드가 `intent`를 읽으면 동작)
  - FALLBACK 계열 action은 intent="FALLBACK"으로 노출
"""

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from agents.router_agent import (
    SEOUL_DISTRICTS,
    RouterAgent,
    _ALLOWED_MAX_CLASS_NAMES,
    _ALLOWED_SERVICE_STATUSES,
)
from llm.client import get_chat_model
from llm.prompts.triage import TRIAGE_FEW_SHOT, TRIAGE_SYSTEM
from schemas.state import ActionType, IntentType


class TriageOutput(BaseModel):
    """TriageAgent 구조화 출력.

    action 결정 + retrieval_intent(RETRIEVE일 때만) + post-filter + user_rationale.
    기존 _IntentOutput과 하위호환을 위해 `intent` 필드를 동기화한다:
      - action=RETRIEVE → intent = primary_intent
      - 그 외 → intent = FALLBACK (기존 분기 로직이 FALLBACK으로 처리하도록)
    """

    reasoning: str | None = Field(
        default=None,
        description="의도 분류와 필터 매핑 근거 (CoT, 내부 전용)",
    )
    # ── 신규 2축 ──
    action: ActionType = Field(description="취할 행동 (5종)")
    primary_intent: IntentType | None = Field(
        default=None,
        description="RETRIEVE일 때만 채움 (검색 방식)",
    )
    secondary_intent: IntentType | None = Field(
        default=None,
        description="SQL↔VECTOR 경계 모호 시만 채움, 그 외 null",
    )
    out_of_scope_type: Literal["domain_outside", "attribute_gap"] | None = Field(
        default=None,
        description="action=OUT_OF_SCOPE일 때 서브타입",
    )
    user_rationale: str | None = Field(
        default=None,
        description="사용자에게 보여줄 판단 근거 1문장",
    )

    # ── 기존 하위호환 필드 ──
    # action=RETRIEVE일 때 primary_intent와 동기화. 그 외 FALLBACK.
    intent: IntentType = Field(default=IntentType.FALLBACK)
    refined_query: str | None = None
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    payment_type: str | None = None
    vector_sub_intent: Literal["identification", "detail", "semantic"] | None = None

    # ── 검증자 ──

    @field_validator("max_class_name", mode="before")
    @classmethod
    def _validate_max_class_name(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in _ALLOWED_MAX_CLASS_NAMES else None  # type: ignore[return-value]

    @field_validator("area_name", mode="before")
    @classmethod
    def _validate_area_name(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in SEOUL_DISTRICTS else None  # type: ignore[return-value]

    @field_validator("service_status", mode="before")
    @classmethod
    def _validate_service_status(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in _ALLOWED_SERVICE_STATUSES else None  # type: ignore[return-value]

    @field_validator("payment_type", mode="before")
    @classmethod
    def _validate_payment_type(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        normalized = v.strip().lower()
        if not normalized:
            return None
        if "무료" in normalized or "free" in normalized or "공짜" in normalized:
            return "무료"
        if "유료" in normalized or "paid" in normalized or "요금" in normalized:
            return "유료"
        return None

    @field_validator("vector_sub_intent", mode="before")
    @classmethod
    def _validate_vector_sub_intent(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in {"identification", "detail", "semantic"} else None  # type: ignore[return-value]

    @field_validator("secondary_intent", mode="before")
    @classmethod
    def _validate_secondary_intent(cls, v: object) -> IntentType | None:
        """secondary_intent는 SQL_SEARCH 또는 VECTOR_SEARCH만 허용한다."""
        if v is None:
            return None
        allowed = {IntentType.SQL_SEARCH.value, IntentType.VECTOR_SEARCH.value}
        if isinstance(v, str) and v in allowed:
            return IntentType(v)
        if isinstance(v, IntentType) and v in (
            IntentType.SQL_SEARCH,
            IntentType.VECTOR_SEARCH,
        ):
            return v
        return None

    def model_post_init(self, __context: object) -> None:
        """action 결정 후 intent 필드를 동기화한다."""
        if self.action == ActionType.RETRIEVE and self.primary_intent is not None:
            # intent를 primary_intent와 동기화 (하위호환)
            object.__setattr__(self, "intent", self.primary_intent)
        else:
            object.__setattr__(self, "intent", IntentType.FALLBACK)


class TriageAgent:
    """LCEL 기반 트리아지 에이전트.

    RouterAgent를 대체하며 2축(action + retrieval_intent) 분류를 수행한다.
    history 컨텍스트 블록은 RouterAgent의 _build_context_block을 재사용한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()
        # 컨텍스트 블록 빌더는 RouterAgent 로직을 재사용한다.
        self._build_context_block = RouterAgent._build_context_block.__get__(
            self, TriageAgent
        )

    async def classify(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
        prev_reasoning: str | None = None,
    ) -> TriageOutput:
        """사용자 메시지를 트리아지하여 TriageOutput을 반환한다.

        Args:
            message: 사용자 원본 발화.
            history: 직전 N턴 대화 이력(과거→최신).
            prev_reasoning: 직전 턴의 판단 근거. EXPLAIN action 가능 여부 판정에 사용.
        """
        context_block = self._build_context_block(history)
        system_parts = [TRIAGE_SYSTEM]
        if context_block:
            system_parts.append(context_block)
        # prev_reasoning 컨텍스트: EXPLAIN 판정을 위해 시스템 프롬프트에 추가
        if prev_reasoning:
            system_parts.append(
                f"직전 답변의 판단 근거(prev_reasoning):\n{prev_reasoning}"
            )
        system_text = "\n\n".join(system_parts)

        messages = [
            SystemMessage(content=system_text),
            *TRIAGE_FEW_SHOT.format_messages(),
            HumanMessage(content=f"사용자 메시지: {message}"),
        ]
        structured = self._llm.with_structured_output(TriageOutput)
        result: TriageOutput = await structured.ainvoke(messages)
        return result
