"""RouterAgent 단위 테스트.

실제 LLM 호출 없이 Mock LLM으로 의도 분류 동작을 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock

from agents.router_agent import RouterAgent, _IntentOutput
from schemas.state import IntentType


def _make_agent(intent: IntentType) -> RouterAgent:
    """지정된 intent를 반환하는 Mock LLM이 주입된 RouterAgent."""
    agent = RouterAgent.__new__(RouterAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    return agent


class TestRouterAgent:
    async def test_classify_returns_sql_search(self):
        """SQL_SEARCH 의도가 반환된다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        result = await agent.classify("지금 접수 중인 수영장 알려줘")

        assert result.intent == IntentType.SQL_SEARCH

    async def test_classify_returns_vector_search(self):
        """VECTOR_SEARCH 의도가 반환된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)
        result = await agent.classify("아이랑 체험할 수 있는 시설 추천")

        assert result.intent == IntentType.VECTOR_SEARCH

    async def test_classify_returns_map(self):
        """MAP 의도가 반환된다."""
        agent = _make_agent(IntentType.MAP)
        result = await agent.classify("내 주변 체육관 지도로 보여줘")

        assert result.intent == IntentType.MAP

    async def test_classify_returns_fallback(self):
        """FALLBACK 의도가 반환된다."""
        agent = _make_agent(IntentType.FALLBACK)
        result = await agent.classify("안녕하세요")

        assert result.intent == IntentType.FALLBACK

    async def test_chain_receives_message(self):
        """classify가 LLM에 message를 HumanMessage로 전달한다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        await agent.classify("마포구 문화행사")

        structured = agent._llm.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]
        human_texts = [m.content for m in messages if m.type == "human"]
        assert any("마포구 문화행사" in t for t in human_texts)

    async def test_recent_queries_optional(self):
        """recent_queries는 기본값 None으로 생략 가능하다."""
        agent = _make_agent(IntentType.SQL_SEARCH)

        result = await agent.classify("수영장")

        assert result.intent == IntentType.SQL_SEARCH

    async def test_few_shot_messages_injected(self):
        """classify 호출 시 few-shot 예시 메시지가 SystemMessage 다음에 주입된다."""
        from llm.prompts.router import ROUTER_FEW_SHOT_EXAMPLES

        agent = _make_agent(IntentType.SQL_SEARCH)
        await agent.classify("수영장")

        structured = agent._llm.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]

        # messages 구조: [SystemMessage, *few_shot(HumanMsg+AIMsg 쌍), HumanMessage(actual)]
        assert messages[0].type == "system"
        # few-shot 예시 수 검증 (N쌍 = N*2 메시지)
        assert len(messages) == 1 + len(ROUTER_FEW_SHOT_EXAMPLES) * 2 + 1
        # 마지막 메시지가 실제 사용자 발화
        assert messages[-1].type == "human"
        assert "수영장" in messages[-1].content
