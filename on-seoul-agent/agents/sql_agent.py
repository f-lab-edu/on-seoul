"""SQL Agent — 정형 데이터 조회.

LLM으로 사용자 메시지에서 필터 파라미터를 추출한 뒤,
tools.sql_search를 통해 on_data_reader 세션으로
public_service_reservations를 파라미터화된 SQL로 조회한다.

LLM이 SQL을 직접 생성하지 않으므로 SQL Injection 위험이 없다.
"""

from datetime import date, datetime, timezone

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agents.router_agent import SEOUL_DISTRICTS
from llm.client import get_chat_model
from schemas.state import AgentState
from tools.sql_search import TOP_K as _TOP_K
from tools.sql_search import sql_search

# Router와 동일한 화이트리스트 — LLM이 벗어난 값을 반환하면 None 정규화
_ALLOWED_MAX_CLASS_NAMES: frozenset[str] = frozenset(
    ["체육시설", "문화행사", "시설대관", "교육", "진료"]
)
_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(
    ["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"]
)

_SYSTEM = """\
당신은 서울시 공공서비스 예약 시스템의 검색 파라미터 추출기입니다.
오늘: {today}

사용자 메시지에서 검색 조건을 JSON 필드로 추출하세요.

필드 설명:
- max_class_name     : 대분류 카테고리. 체육시설·문화행사·시설대관·교육·진료 중 하나. 언급 없으면 null.
- area_name          : 서울 자치구 이름 (예: 강남구, 마포구). 언급 없으면 null.
- service_status     : 접수중·예약마감·접수종료·예약일시중지·안내중 중 하나. 언급 없으면 null.
- keyword            : 시설명·장소명 검색 키워드. 카테고리·지역 외 구체적 시설 조건이 있을 때만. 없으면 null.
- receipt_date_from  : 접수 기간 시작 필터 (ISO YYYY-MM-DD). 이 날짜 이후에도 접수가 열려 있는 서비스 포함. 날짜 미언급이면 null.
- receipt_date_to    : 접수 기간 종료 필터 (ISO YYYY-MM-DD). 이 날짜 이전에 접수가 시작된 서비스 포함. 날짜 미언급이면 null.

날짜 변환 기준 (오늘: {today}):
  "오늘"         → from=오늘, to=오늘
  "이번 주"      → from=이번 주 월요일, to=이번 주 일요일
  "이번 달"      → from=이달 1일, to=이달 말일
  "N월"          → from=N월 1일, to=N월 말일
  날짜 미언급    → null / null

추출 불가능한 필드는 반드시 null로 반환하세요.
"""

_HUMAN = "사용자 메시지: {message}"

# ---------------------------------------------------------------------------
# Few-shot 예시
#
# 예시 1: 날짜(이번 주) + 지역 + 카테고리 + 상태
# 예시 2: 날짜(월) + 지역 + 카테고리
# 예시 3: 날짜 없음 + 키워드
# ---------------------------------------------------------------------------
_FEW_SHOT_EXAMPLES = [
    {
        "message": "마포구 이번 주 문화행사 접수 중인 거 보여줘",
        "output": (
            '{{"max_class_name": "문화행사", "area_name": "마포구",'
            ' "service_status": "접수중", "keyword": null,'
            ' "receipt_date_from": "2026-05-18", "receipt_date_to": "2026-05-24"}}'
        ),
    },
    {
        "message": "5월에 접수 시작하는 강남구 교육 프로그램 알려줘",
        "output": (
            '{{"max_class_name": "교육", "area_name": "강남구",'
            ' "service_status": null, "keyword": null,'
            ' "receipt_date_from": "2026-05-01", "receipt_date_to": "2026-05-31"}}'
        ),
    },
    {
        "message": "성동구 테니스장 예약 가능한 곳",
        "output": (
            '{{"max_class_name": "체육시설", "area_name": "성동구",'
            ' "service_status": null, "keyword": "테니스장",'
            ' "receipt_date_from": null, "receipt_date_to": null}}'
        ),
    },
]


class _SqlParams(BaseModel):
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
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


class SqlAgent:
    """LLM 파라미터 추출 + tools.sql_search 위임 에이전트.

    세션은 호출자(워크플로우 또는 테스트)가 주입한다.
    SQL 실행 로직은 tools/sql_search.py에 위임한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()
        from langchain_core.prompts import FewShotChatMessagePromptTemplate

        few_shot = FewShotChatMessagePromptTemplate(
            example_prompt=ChatPromptTemplate.from_messages([
                ("human", "사용자 메시지: {message}"),
                ("ai", "{output}"),
            ]),
            examples=_FEW_SHOT_EXAMPLES,
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system", _SYSTEM),
            few_shot,
            ("human", _HUMAN),
        ])
        self._chain = prompt | llm.with_structured_output(_SqlParams)

    async def search(self, state: AgentState, session: AsyncSession) -> AgentState:
        """메시지에서 파라미터 추출 후 DB 조회. sql_results를 채운 AgentState 반환.

        Router가 이미 post-filter 메타데이터를 산출한 경우
        (state["refined_query"] 존재) max_class_name/area_name/service_status는
        state 값을 우선 사용한다. LLM에는 refined_query(더 짧고 정제된 텍스트)를
        전달해 keyword와 날짜 파라미터만 추출한다.
        """
        today_str = datetime.now(tz=timezone.utc).date().isoformat()
        router_refined = state.get("refined_query")

        # Router refined_query가 있으면 더 짧고 정제된 텍스트를 LLM에 전달
        input_message = router_refined or state["message"]
        params: _SqlParams = await self._chain.ainvoke({
            "message": input_message,
            "today": today_str,
        })

        if router_refined:
            max_class_name = state.get("max_class_name")
            area_name = state.get("area_name")
            service_status = state.get("service_status")
        else:
            max_class_name = params.max_class_name
            area_name = params.area_name
            service_status = params.service_status

        rows = await sql_search(
            session,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            keyword=params.keyword,
            receipt_date_from=params.receipt_date_from,
            receipt_date_to=params.receipt_date_to,
            top_k=_TOP_K,
        )
        return {**state, "sql_results": rows, "sql_keyword": params.keyword}
