"""TriageAgent 통합 테스트 분할 공유 헬퍼.

test_graph_triage_routing / _nodes / _refine 가 공유하는 state/answer_agent 팩토리.
"""

from schemas.state import AgentState
from tests.helpers import make_agent_state, make_answer_agent


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _answer_agent(answer: str = "답변입니다."):
    return make_answer_agent(answer)
