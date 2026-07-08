"""L1 Phase 4 — critic 결정에 따른 thin/skew 톤 조정 게이팅.

`_effective_result_quality`: critic_decision ∈ (None, ANSWER)면 result_quality 를
그대로 적용, STOP/REPLAN 이면 None(톤 억제). 계획서 §3-3 — thin/skew 는 규칙으로
"무조건 톤만"이 아니라 critic 의 케이스별 판단으로 대체된다.

플래그 오프(enable_retrieval_critic=False)에선 critic 이 진입하지 않아 critic_decision
이 항상 None → 기존 톤 조정 동작이 완전히 그대로다(회귀 0).

fake LLM — 실제 LLM 미호출. answer() 통합은 system 프롬프트 절 존재 여부만 assert.
"""

from agents.answer_agent import AnswerAgent, _CLAUSE_SKEW_OFFER
from schemas.state import IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _hydrated(areas, status="접수중"):
    return [
        {
            "service_id": f"P{i}",
            "service_name": f"강남시설{i}",
            "area_name": a,
            "service_status": status,
        }
        for i, a in enumerate(areas)
    ]


_SKEW_RQ = {"skew_field": "area_name", "skew_value": "강남구", "thin": False}


class TestEffectiveResultQuality:
    """_effective_result_quality 단위 — critic 결정별 톤 소스 게이팅."""

    def test_none_decision_applies_quality(self):
        # critic 미진입(플래그 오프 / 명백히 좋음 / fail-open) → 기존 동작 불변.
        rq = {"thin": True}
        state = make_agent_state(result_quality=rq, critic_decision=None)
        assert AnswerAgent._effective_result_quality(state) == rq

    def test_answer_decision_applies_quality(self):
        # critic 이 "이 결과로 답하라" → 톤 조정 적용.
        rq = {"skew_field": "area_name", "skew_value": "강남구"}
        state = make_agent_state(result_quality=rq, critic_decision="ANSWER")
        assert AnswerAgent._effective_result_quality(state) == rq

    def test_stop_decision_suppresses_quality(self):
        # 정직한 한계 안내 프레이밍 우선 → thin/skew 톤 억제.
        state = make_agent_state(result_quality={"thin": True}, critic_decision="STOP")
        assert AnswerAgent._effective_result_quality(state) is None

    def test_replan_decision_suppresses_quality(self):
        # REPLAN 라운드는 answer 에 도달하지 않지만, 방어적으로도 톤 억제.
        state = make_agent_state(
            result_quality={"thin": True}, critic_decision="REPLAN"
        )
        assert AnswerAgent._effective_result_quality(state) is None


class TestCriticToneGateInAnswer:
    """answer() end-to-end — critic 결정이 SKEW 톤 절 적용/억제를 좌우한다."""

    async def _skew_system(self, critic_decision: str | None) -> str:
        agent = make_answer_agent("강남구 체육시설 안내입니다.")
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            message="지금 접수중 체육시설",
            hydrated_services=_hydrated(["강남구"] * 5),
            result_quality=_SKEW_RQ,
            critic_decision=critic_decision,
        )
        await agent.answer(state)
        return agent._answer_chain.ainvoke.call_args[0][0]["system"]

    async def test_flag_off_none_applies_skew_tone(self):
        # 플래그 오프/미진입(critic_decision=None) → SKEW 톤 유지(회귀 0).
        system = await self._skew_system(None)
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") in system

    async def test_critic_answer_applies_skew_tone(self):
        system = await self._skew_system("ANSWER")
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") in system

    async def test_critic_stop_suppresses_skew_tone(self):
        system = await self._skew_system("STOP")
        assert _CLAUSE_SKEW_OFFER.format(skew_value="강남구") not in system
