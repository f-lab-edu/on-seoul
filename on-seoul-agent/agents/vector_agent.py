"""Vector Agent — 의미 기반 유사도 검색.

1. LLM으로 사용자 질의를 검색에 최적화된 문장으로 정제한다.
2. 정제된 질의를 임베딩한다.
3. on_ai_app 세션으로 service_embeddings에서 코사인 유사도 상위 K개를 조회한다.
"""

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from llm.client import get_chat_model, get_embeddings
from schemas.state import AgentState
from tools.vector_search import vector_search

_REFINE_SYSTEM = """\
당신은 서울시 공공서비스 예약 검색 전문가입니다.
사용자 질의를 벡터 유사도 검색에 적합한 명확하고 구체적인 검색 문장으로 변환하세요.
시설 유형, 대상, 활동 특성을 포함하면 검색 품질이 높아집니다.
한국어로 2-3 문장 이내로 작성하세요.
"""

_REFINE_HUMAN = "사용자 질의: {message}"


class _RefinedQuery(BaseModel):
    refined_query: str


class VectorAgent:
    """질의 정제 → 임베딩 → pgvector 유사도 검색 에이전트.

    ai_session : on_ai_app 계정 세션 (service_embeddings CRUD 권한)
    """

    def __init__(
        self,
        model: BaseChatModel | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        llm = model or get_chat_model()
        self._embeddings = embeddings or get_embeddings()
        prompt = ChatPromptTemplate.from_messages([
            ("system", _REFINE_SYSTEM),
            ("human", _REFINE_HUMAN),
        ])
        self._refine_chain = prompt | llm.with_structured_output(_RefinedQuery)

    async def search(self, state: AgentState, session: AsyncSession) -> AgentState:
        """질의 정제 → 임베딩 → 유사도 검색. vector_results와 refined_query를 채운 AgentState 반환."""
        refined: _RefinedQuery = await self._refine_chain.ainvoke(
            {"message": state["message"]}
        )
        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        # TODO(Phase 15): Router Agent가 AgentState에 max_class_name/area_name/service_status를
        # 채우면 여기서 state에서 추출하여 pre-filter로 전달한다.
        rows = await vector_search(session, query_vector)
        return {
            **state,
            "refined_query": refined.refined_query,
            "vector_results": rows,
        }
