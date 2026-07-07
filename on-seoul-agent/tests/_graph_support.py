"""AgentGraph 테스트 분할 파일이 공유하는 픽스처 헬퍼.

test_graph_routing / test_graph_correction / test_graph_stream /
test_graph_propagation 가 공통으로 쓰는 _state, _vector_agent 및 별칭을 모은다.
"""

from unittest.mock import AsyncMock, MagicMock

from agents.vector_agent import VectorAgent, _RefinedQuery
from schemas.state import AgentState
from tests.helpers import (
    make_agent_state,
    make_ai_session,
    make_answer_agent,
    make_router,
    make_sql_agent,
)


# ---------------------------------------------------------------------------
# 픽스처 헬퍼 — 그래프 테스트 전용
# ---------------------------------------------------------------------------


def _state(**kwargs) -> AgentState:
    return make_agent_state(**kwargs)


# 공유 헬퍼 별칭 (가독성)
_router = make_router
_sql_agent = make_sql_agent
_answer_agent = make_answer_agent
_ai_session = make_ai_session


def _vector_agent(rows: list[dict]) -> tuple[VectorAgent, MagicMock, AsyncMock]:
    """rows 를 vector_search 결과로 반환하는 VectorAgent mock + ai_session + bm25 mock."""
    agent = VectorAgent.__new__(VectorAgent)

    refine_chain = MagicMock()
    refine_chain.ainvoke = AsyncMock(
        return_value=_RefinedQuery(
            refined_query="정제된 질의",
            max_class_name=None,
            area_name=None,
            service_status=None,
        )
    )
    agent._refine_chain = refine_chain

    embeddings = MagicMock()
    embeddings.aembed_query = AsyncMock(return_value=[0.1] * 3)
    agent._embeddings = embeddings

    mock_bm25 = AsyncMock(return_value=[])
    return agent, make_ai_session(), mock_bm25
