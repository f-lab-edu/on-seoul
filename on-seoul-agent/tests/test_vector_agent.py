"""VectorAgent 단위 테스트.

질의 정제 체인, 임베딩, pgvector 유사도 검색 동작을 Mock으로 검증한다.
_similarity_search 메서드가 제거되고 tools.vector_search.vector_search를
위임 호출하는 구조이므로, 해당 함수를 patch하여 검증한다.
"""

from unittest.mock import AsyncMock, MagicMock, patch


from agents.vector_agent import VectorAgent, _RefinedQuery
from schemas.state import AgentState, IntentType


def _make_state(message: str = "아이랑 체험할 수 있는 시설") -> AgentState:
    return AgentState(
        room_id=1,
        message_id=1,
        message=message,
        title_needed=False,
        intent=IntentType.VECTOR_SEARCH,
        refined_query=None,
        sql_results=None,
        vector_results=None,
        map_results=None,
        answer=None,
        title=None,
        trace=None,
        error=None,
    )


def _make_agent(
    refined_query: str,
    vector: list[float],
) -> VectorAgent:
    """VectorAgent를 생성한다. DB 세션과 vector_search는 개별 테스트에서 patch."""
    agent = VectorAgent.__new__(VectorAgent)

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=_RefinedQuery(refined_query=refined_query))
    agent._refine_chain = mock_chain

    mock_embeddings = MagicMock()
    mock_embeddings.aembed_query = AsyncMock(return_value=vector)
    agent._embeddings = mock_embeddings

    return agent


class TestVectorAgent:
    async def test_search_populates_vector_results(self):
        """search는 vector_search 반환값을 vector_results에 채운다."""
        rows = [{"service_id": "S001", "service_name": "어린이 체험관", "similarity": 0.85}]
        agent = _make_agent("어린이 체험 시설", [0.1, 0.2])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=rows)):
            result = await agent.search(_make_state(), mock_session)

        assert result["vector_results"] == rows

    async def test_search_populates_refined_query(self):
        """search는 정제된 질의를 refined_query에 채운다."""
        agent = _make_agent("어린이 체험 시설", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])):
            result = await agent.search(_make_state(), mock_session)

        assert result["refined_query"] == "어린이 체험 시설"

    async def test_refine_chain_receives_original_message(self):
        """정제 체인에 원본 메시지가 전달된다."""
        agent = _make_agent("정제된 쿼리", [0.1])
        state = _make_state("조용한 운동 시설")
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])):
            await agent.search(state, mock_session)

        agent._refine_chain.ainvoke.assert_called_once_with({"message": "조용한 운동 시설"})

    async def test_embedding_called_with_refined_query(self):
        """임베딩은 원본이 아닌 정제된 질의로 호출된다."""
        agent = _make_agent("아이 동반 체험 시설 서울", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])):
            await agent.search(_make_state(), mock_session)

        agent._embeddings.aembed_query.assert_called_once_with("아이 동반 체험 시설 서울")

    async def test_search_preserves_state_fields(self):
        """search는 vector_results, refined_query 외 나머지 state를 보존한다."""
        agent = _make_agent("정제됨", [0.1])
        state = _make_state()
        state["room_id"] = 99
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])):
            result = await agent.search(state, mock_session)

        assert result["room_id"] == 99
        assert result["message"] == "아이랑 체험할 수 있는 시설"

    async def test_similarity_search_passes_query_vector(self):
        """vector_search에 query_vector가 전달된다."""
        vector = [0.1, 0.2, 0.3]
        agent = _make_agent("정제된 쿼리", vector)
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs:
            await agent.search(_make_state(), mock_session)
            _, call_vector = mock_vs.call_args[0]
            assert call_vector == vector

    async def test_search_returns_empty_vector_results_when_no_rows(self):
        """vector_search가 빈 리스트를 반환하면 vector_results는 빈 리스트다 (None이 아님)."""
        agent = _make_agent("정제된 쿼리", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])):
            result = await agent.search(_make_state(), mock_session)

        assert result["vector_results"] == []
        assert result["vector_results"] is not None

    async def test_similarity_search_passes_session(self):
        """vector_search에 session이 첫 번째 인자로 전달된다."""
        agent = _make_agent("쿼리", [0.1])
        mock_session = MagicMock()

        with patch("agents.vector_agent.vector_search", new=AsyncMock(return_value=[])) as mock_vs:
            await agent.search(_make_state(), mock_session)
            call_session, _ = mock_vs.call_args[0]
            assert call_session is mock_session
