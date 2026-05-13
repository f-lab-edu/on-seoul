"""SQL Agent — 정형 데이터 조회.

LLM으로 사용자 메시지에서 필터 파라미터를 추출한 뒤,
tools.sql_search를 통해 on_data_reader 세션으로
public_service_reservations를 파라미터화된 SQL로 조회한다.

LLM이 SQL을 직접 생성하지 않으므로 SQL Injection 위험이 없다.
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from llm.client import get_chat_model
from schemas.state import AgentState
from tools.sql_search import TOP_K as _TOP_K
from tools.sql_search import sql_search

_SYSTEM = """\
당신은 서울시 공공서비스 예약 시스템의 검색 파라미터 추출기입니다.
사용자 메시지에서 검색 조건을 JSON 필드로 추출하세요.

필드 설명:
- max_class_name : 대분류 카테고리. 체육시설·문화행사·시설대관·교육·진료 중 하나. 언급 없으면 null.
- area_name      : 서울 자치구 이름 (예: 강남구, 마포구). 언급 없으면 null.
- service_status : 접수중·예약마감·접수종료·예약일시중지·안내중 중 하나. 언급 없으면 null.
- keyword        : 시설명·장소명 검색 키워드. 그 외 구체적 조건이 없으면 null.

추출 불가능한 필드는 반드시 null로 반환하세요.
"""

_HUMAN = "사용자 메시지: {message}"


class _SqlParams(BaseModel):
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    keyword: str | None = None


class SqlAgent:
    """LLM 파라미터 추출 + tools.sql_search 위임 에이전트.

    세션은 호출자(워크플로우 또는 테스트)가 주입한다.
    SQL 실행 로직은 tools/sql_search.py에 위임한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
            ("human", _HUMAN),
        ])
        self._chain = prompt | llm.with_structured_output(_SqlParams)

    async def search(self, state: AgentState, session: AsyncSession) -> AgentState:
        """메시지에서 파라미터 추출 후 DB 조회. sql_results를 채운 AgentState 반환."""
        params: _SqlParams = await self._chain.ainvoke({"message": state["message"]})
        rows = await sql_search(
            session,
            max_class_name=params.max_class_name,
            area_name=params.area_name,
            service_status=params.service_status,
            keyword=params.keyword,
            top_k=_TOP_K,
        )
        return {**state, "sql_results": rows}
