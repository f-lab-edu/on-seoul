"""ambiguous_node 단위 테스트 — clarify() 위임 + 노드 폴백.

ambiguous_node는 AnswerAgent.clarify()를 호출해 명확화 질문을 생성하고,
clarify()가 예외를 던져도 노드 차원에서 폴백 답변 + ambiguous_error
node_path로 graceful degrade한다. fake LLM으로 hermetic하게 검증한다.
"""

from unittest.mock import AsyncMock

from agents.nodes import GraphNodes
from schemas.state import ActionType, AgentState
from tests.helpers import make_agent_state, make_answer_agent, make_triage


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


def _nodes(answer_agent) -> GraphNodes:
    return GraphNodes(
        triage=make_triage(ActionType.AMBIGUOUS), answer_agent=answer_agent
    )


class TestAmbiguousNode:
    async def test_calls_clarify_and_sets_answer_no_cards(self):
        agent = make_answer_agent("어느 시설을 말씀하시는 건가요?")
        nodes = _nodes(agent)

        update = await nodes.ambiguous_node(
            _state(
                message="거기 주말에도 해?",
                history=[{"role": "user", "content": "강남구 체육시설"}],
            )
        )

        assert update["answer"] == "어느 시설을 말씀하시는 건가요?"
        assert update["service_cards"] == []
        assert update["node_path"] == ["ambiguous_node"]

    async def test_passes_history_through_to_clarify(self):
        """history는 state를 통째로 clarify에 전달되어 system 컨텍스트로 들어간다."""
        agent = make_answer_agent("무엇을 찾으시나요?")
        nodes = _nodes(agent)

        await nodes.ambiguous_node(
            _state(
                message="거기 또 알려줘",
                history=[{"role": "user", "content": "마포구 풋살장"}],
            )
        )

        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert "마포구 풋살장" in system

    async def test_node_falls_back_on_clarify_exception(self):
        agent = make_answer_agent()
        agent.clarify = AsyncMock(side_effect=RuntimeError("boom"))
        nodes = _nodes(agent)

        update = await nodes.ambiguous_node(_state(message="좋은 곳", history=[]))

        assert update["node_path"] == ["ambiguous_error"]
        assert update["error"]
        assert update["answer"]  # 사용자 응답이 비지 않는다.
