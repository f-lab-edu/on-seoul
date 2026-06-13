"""SQL Agent — 정형 데이터 조회.

LLM으로 사용자 메시지에서 필터 파라미터를 추출한 뒤,
tools.sql_search를 통해 on_data_reader 세션으로
public_service_reservations를 파라미터화된 SQL로 조회한다.

LLM이 SQL을 직접 생성하지 않으므로 SQL Injection 위험이 없다.
"""

from datetime import date, datetime, timezone

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agents.router_agent import _ALLOWED_PAYMENT_TYPES, SEOUL_DISTRICTS
from llm.client import get_chat_model
from llm.prompts.sql_extraction import (
    SQL_EXTRACTION_FEW_SHOT_EXAMPLES,
    SQL_EXTRACTION_HUMAN,
    SQL_EXTRACTION_SYSTEM,
)
from schemas.state import AgentState
from tools.sql_search import TOP_K as _TOP_K
from tools.sql_search import sql_search

# Router와 동일한 화이트리스트 — LLM이 벗어난 값을 반환하면 None 정규화
_ALLOWED_MAX_CLASS_NAMES: frozenset[str] = frozenset(
    ["체육시설", "문화체험", "공간시설", "교육강좌", "진료복지"]
)
_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(
    ["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"]
)
# payment_type 정규값은 router_agent 단일 정의를 재사용 (eval-운영 정합성).


class _SqlParams(BaseModel):
    reasoning: str | None = Field(
        default=None,
        description="날짜 표현이 있을 때 오늘 기준 계산 과정 (내부 CoT용, 검색 쿼리에는 미사용)",
    )
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    payment_type: str | None = None
    keyword: str | None = None
    receipt_date_from: date | None = None
    receipt_date_to: date | None = None

    @field_validator("max_class_name", mode="before")
    @classmethod
    def _validate_max_class_name(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in _ALLOWED_MAX_CLASS_NAMES else None  # type: ignore[return-value]

    @field_validator("area_name", mode="before")
    @classmethod
    def _validate_area_name(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in SEOUL_DISTRICTS else None  # type: ignore[return-value]

    @field_validator("service_status", mode="before")
    @classmethod
    def _validate_service_status(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in _ALLOWED_SERVICE_STATUSES else None  # type: ignore[return-value]

    @field_validator("payment_type", mode="before")
    @classmethod
    def _validate_payment_type(cls, v: object) -> str | None:
        if v is None:
            return None
        return v if v in _ALLOWED_PAYMENT_TYPES else None  # type: ignore[return-value]


class SqlAgent:
    """LLM 파라미터 추출 + tools.sql_search 위임 에이전트.

    세션은 호출자(워크플로우 또는 테스트)가 주입한다.
    SQL 실행 로직은 tools/sql_search.py에 위임한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        # temperature=0: 날짜 계산·카테고리 분류 등 결정론적 추출 — 창의성 불필요
        llm = model or get_chat_model(temperature=0)

        few_shot = FewShotChatMessagePromptTemplate(
            example_prompt=ChatPromptTemplate.from_messages(
                [
                    ("human", "사용자 메시지: {message}"),
                    ("ai", "{output}"),
                ]
            ),
            examples=SQL_EXTRACTION_FEW_SHOT_EXAMPLES,
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", SQL_EXTRACTION_SYSTEM),
                few_shot,
                ("human", SQL_EXTRACTION_HUMAN),
            ]
        )
        self._chain = prompt | llm.with_structured_output(_SqlParams)

    async def search(
        self,
        state: AgentState,
        session: AsyncSession,
        *,
        top_k: int | None = None,
    ) -> AgentState:
        """메시지에서 파라미터 추출 후 DB 조회. sql_results를 채운 AgentState 반환.

        Router가 이미 post-filter 메타데이터를 산출한 경우
        (state["refined_query"] 존재) max_class_name/area_name/service_status는
        state 값을 우선 사용한다. LLM에는 refined_query(더 짧고 정제된 텍스트)를
        전달해 keyword와 날짜 파라미터만 추출한다.

        Parameters
        ----------
        top_k:
            반환 결과 수 상한. None이면 기본값(_TOP_K=10) 사용.
            평가 스크립트처럼 더 많은 후보가 필요한 경우에만 지정한다.
        """
        today_str = datetime.now(tz=timezone.utc).date().isoformat()
        plan = state.get("plan") or {}
        filters = state.get("filters") or {}
        router_refined = plan.get("refined_query")

        # Router refined_query가 있으면 더 짧고 정제된 텍스트를 LLM에 전달
        input_message = router_refined or state["message"]
        params: _SqlParams = await self._chain.ainvoke(
            {
                "message": input_message,
                "today": today_str,
            }
        )

        if router_refined:
            max_class_name = filters.get("max_class_name")
            area_name = filters.get("area_name")
            service_status = filters.get("service_status")
            payment_type = filters.get("payment_type")
        else:
            max_class_name = params.max_class_name
            area_name = params.area_name
            service_status = params.service_status
            payment_type = params.payment_type

        rows = await sql_search(
            session,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            payment_type=payment_type,
            keyword=params.keyword,
            receipt_date_from=params.receipt_date_from,
            receipt_date_to=params.receipt_date_to,
            top_k=top_k if top_k is not None else _TOP_K,
        )
        return {**state, "sql": {"results": rows, "keyword": params.keyword}}
