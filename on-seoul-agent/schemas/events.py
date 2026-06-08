from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class EventType(str, Enum):
    AGENT_START = "agent_start"
    TOOL_CALL = "tool_call"
    TOKEN = "token"
    DONE = "done"
    ERROR = "error"


class SSEEvent(BaseModel):
    event: EventType
    data: Any = None
    message_id: int | None = None


class DecisionEvent(BaseModel):
    """Triage 완료 직후 SSE로 방출되는 판단 근거 이벤트.

    action    — TriageAgent가 결정한 행동 유형 (RETRIEVE|DIRECT_ANSWER|AMBIGUOUS|OUT_OF_SCOPE|EXPLAIN)
    routes    — [primary_intent, secondary_intent] 에서 None 제거한 리스트. RETRIEVE 외에는 [].
    user_rationale — TriageAgent가 산출한 사용자용 근거 1문장 (최대 200자).
    sources   — 검색 전 단계라 항상 [] (W3 심화 단계에서 채울 예정).
    """

    event: Literal["decision"] = "decision"
    action: str
    routes: list[str]
    user_rationale: str
    sources: list[dict[str, Any]] = []
