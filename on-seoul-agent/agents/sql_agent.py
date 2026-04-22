"""SQL Agent — 정형 데이터 조회.

LLM으로 사용자 메시지에서 필터 파라미터를 추출한 뒤,
on_data_reader 세션으로 public_service_reservations를 파라미터화된 SQL로 조회한다.

LLM이 SQL을 직접 생성하지 않으므로 SQL Injection 위험이 없다.
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm.client import get_chat_model
from schemas.state import AgentState

_SYSTEM = """\
당신은 서울시 공공서비스 예약 시스템의 검색 파라미터 추출기입니다.
사용자 메시지에서 검색 조건을 JSON 필드로 추출하세요.

필드 설명:
- max_class_name : 대분류 카테고리. 체육시설·문화행사·시설대관·교육·진료 중 하나. 언급 없으면 null.
- area_name      : 서울 자치구 이름 (예: 강남구, 마포구). 언급 없으면 null.
- service_status : 접수중·접수예정·마감·대기 중 하나. 언급 없으면 null.
- keyword        : 시설명·장소명 검색 키워드. 그 외 구체적 조건이 없으면 null.

추출 불가능한 필드는 반드시 null로 반환하세요.
"""

_HUMAN = "사용자 메시지: {message}"

_RESULT_COLUMNS = """
    service_id, service_name, max_class_name, min_class_name,
    area_name, place_name, service_status, payment_type,
    service_url, receipt_start_dt, receipt_end_dt,
    service_open_start_dt, service_open_end_dt,
    coord_x, coord_y, target_info
"""

_TOP_K = 10


class _SqlParams(BaseModel):
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    keyword: str | None = None


class SqlAgent:
    """LLM 파라미터 추출 + 파라미터화 SQL 조회 에이전트.

    세션은 호출자(워크플로우 또는 테스트)가 주입한다.
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
        rows = await self._query(session, params)
        return {**state, "sql_results": rows}

    async def _query(self, session: AsyncSession, params: _SqlParams) -> list[dict]:
        conditions = ["deleted_at IS NULL"]
        bind: dict = {}

        if params.max_class_name:
            conditions.append("max_class_name = :max_class_name")
            bind["max_class_name"] = params.max_class_name

        if params.area_name:
            conditions.append("area_name = :area_name")
            bind["area_name"] = params.area_name

        if params.service_status:
            conditions.append("service_status = :service_status")
            bind["service_status"] = params.service_status

        if params.keyword:
            conditions.append(
                "(service_name ILIKE :keyword OR place_name ILIKE :keyword)"
            )
            bind["keyword"] = f"%{params.keyword}%"

        where = " AND ".join(conditions)
        sql = f"""
            SELECT {_RESULT_COLUMNS}
            FROM public_service_reservations
            WHERE {where}
            ORDER BY receipt_start_dt DESC NULLS LAST
            LIMIT {_TOP_K}
        """

        result = await session.execute(text(sql), bind)
        keys = result.keys()
        return [dict(zip(keys, row)) for row in result.fetchall()]
