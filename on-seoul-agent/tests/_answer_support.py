"""AnswerAgent 테스트 공유 헬퍼.

test_answer_agent_*.py 분할 파일들이 공유하는 _make_state / _make_agent.
본문은 원본 test_answer_agent.py에서 그대로 이동했다(불변).
"""

from unittest.mock import AsyncMock, MagicMock

from tests.helpers import make_agent_state
from agents.answer_agent import (
    AnswerAgent,
    _compose,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_ANALYTICS,
    _STRUCT_FALLBACK,
    _STRUCT_MAP,
    _FALLBACK_GUARDRAILS,
)
from schemas.state import AgentState, IntentType


def _make_state(**kwargs) -> AgentState:
    return make_agent_state(intent=IntentType.SQL_SEARCH, **kwargs)


def _make_agent(
    answer_text: str = "수영장 목록입니다.",
) -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)

    mock_answer_chain = MagicMock()
    mock_answer_chain.ainvoke = AsyncMock(return_value=answer_text)
    agent._answer_chain = mock_answer_chain

    # Tier 1 정적 프롬프트 캐시 — 실제 __init__과 동일한 값으로 초기화.
    agent._static_prompts = {
        IntentType.MAP.value: _compose(_ROLE, _STRUCT_MAP, _OUTPUT_RULES),
        IntentType.ANALYTICS.value: _compose(_ROLE, _STRUCT_ANALYTICS, _OUTPUT_RULES),
        IntentType.FALLBACK.value: _compose(
            _ROLE, _STRUCT_FALLBACK, _FALLBACK_GUARDRAILS, _OUTPUT_RULES
        ),
    }

    return agent
