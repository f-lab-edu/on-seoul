"""Router Agent — 사용자 의도 분류.

LCEL 체인으로 사용자 메시지를 분석해 IntentType 4종 중 하나로 분류한다.
  - SQL_SEARCH  : 카테고리·지역·날짜·상태 등 정형 조건 기반 조회
  - VECTOR_SEARCH: 의미 기반(유사도) 검색
  - MAP         : 지도·위치·반경 탐색
  - FALLBACK    : 위 세 가지에 해당하지 않는 일반 안내
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from llm.client import get_chat_model
from schemas.state import AgentState, IntentType

_SYSTEM = """\
당신은 서울시 공공서비스 예약 챗봇의 라우터입니다.
사용자 메시지를 읽고 아래 네 가지 의도 중 하나를 반환하세요.

SQL_SEARCH   - 카테고리(체육시설·문화행사·시설대관·교육·진료), 자치구, 접수 상태, 날짜 등
               구체적 조건으로 시설/서비스를 조회하는 경우
               예) "지금 접수 중인 수영장", "마포구 이번 주 문화행사"
VECTOR_SEARCH - 키워드나 의미로 비슷한 시설을 찾는 경우
               예) "아이랑 체험할 수 있는 곳", "조용한 운동 시설"
MAP          - 지도, 위치, 반경, 근처 시설을 묻는 경우
               예) "내 주변 500m 이내 체육관", "지도로 보여줘"
FALLBACK     - 위 세 가지에 해당하지 않는 경우 (인사, 기능 문의 등)
"""

_HUMAN = "사용자 메시지: {message}"


class _IntentOutput(BaseModel):
    intent: IntentType


class RouterAgent:
    """LCEL 기반 의도 분류 에이전트.

    LLM의 with_structured_output으로 IntentType을 직접 추출한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
            ("human", _HUMAN),
        ])
        self._chain = prompt | llm.with_structured_output(_IntentOutput)

    async def classify(self, state: AgentState) -> AgentState:
        """사용자 메시지를 분석해 intent를 채운 새 AgentState를 반환한다."""
        result: _IntentOutput = await self._chain.ainvoke({"message": state["message"]})
        return {**state, "intent": result.intent}
