"""non-RETRIEVE 강건성 테스트 공유 헬퍼.

`test_non_retrieve_robustness.py` 분할 시 두 파일이 공유하는 state/intake 빌더.
"""

from schemas.intake import IntakeAction, TurnKind
from schemas.state import AgentState
from tests.helpers import make_agent_state, make_intake


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _attribute_gap_intake():
    return make_intake(
        turn_kind=TurnKind.NEW,
        action=IntakeAction.OUT_OF_SCOPE,
        oos_type="attribute_gap",
        user_rationale="특정 시설 식별이 필요합니다.",
    )
