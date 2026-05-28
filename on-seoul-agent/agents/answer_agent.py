"""Answer Agent — 자연어 답변 + 시설 카드 가공.

AgentState의 검색 결과(sql_results / vector_results / map_results)를 종합해
사용자에게 전달할 최종 답변과 시설 카드 목록을 생성한다.

title_needed=True 인 경우(첫 메시지) 대화 제목도 함께 생성한다.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from llm.client import get_chat_model
from schemas.state import AgentState

# ---------------------------------------------------------------------------
# 답변 생성 프롬프트
# ---------------------------------------------------------------------------

_ANSWER_SYSTEM = """\
당신은 서울시 공공서비스 예약 안내 챗봇입니다.
아래 검색 결과를 바탕으로 사용자 질문에 친절하고 간결하게 답변하세요.

규칙:
- 검색 결과가 없으면 "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다."라고 답하세요.
- 결과가 6건 이상이면 상위 5건만 상세 안내하고, 나머지는 "외 N건"으로만 안내하세요.
- 각 시설은 반드시 아래 형식을 따르세요 (제공된 값이 있을 때만 해당 줄을 출력, 없는 줄은 생략):

  • {service_name} ({area_name} {place_name})
    - 분류: {max_class_name} > {min_class_name}
    - 요금: {payment_type} / 대상: {target_info}
    - 접수 상태: {service_status} ({receipt_start_dt} ~ {receipt_end_dt})
    - 이용 기간: {service_open_start_dt} ~ {service_open_end_dt}
    - 바로가기: {service_url}

- service_url 은 시설별 고유 링크입니다. 반드시 해당 시설의 service_url 값을 그대로 출력하세요.
  값이 비어 있는 경우에만 https://yeyak.seoul.go.kr 를 안내합니다.
  모든 시설을 yeyak.seoul.go.kr 로 일괄 안내하는 것은 금지합니다.
- 날짜는 'YYYY-MM-DD' 형태로 표시하고, 시간 부분은 생략합니다.
- 마크다운 없이 자연스러운 한국어 줄바꿈으로 작성하세요.
"""

_ANSWER_HUMAN = """\
사용자 질문: {message}

검색 결과:
{results_json}
"""

# ---------------------------------------------------------------------------
# 제목 생성 프롬프트
# ---------------------------------------------------------------------------

_TITLE_SYSTEM = """\
사용자 질문을 보고 대화 제목을 10자 이내로 만드세요.
특수문자나 이모지 없이 명사형으로 끝내세요.
"""

_TITLE_HUMAN = "사용자 질문: {message}"

_FALLBACK_URL = "https://yeyak.seoul.go.kr"


class _AnswerOutput(BaseModel):
    answer: str


class _TitleOutput(BaseModel):
    title: str


class AnswerAgent:
    """검색 결과 → 자연어 답변 + 시설 카드 + (선택) 제목 생성 에이전트."""

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()

        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _ANSWER_SYSTEM),
                ("human", _ANSWER_HUMAN),
            ]
        )
        self._answer_chain = answer_prompt | llm.with_structured_output(_AnswerOutput)

        title_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _TITLE_SYSTEM),
                ("human", _TITLE_HUMAN),
            ]
        )
        self._title_chain = title_prompt | llm.with_structured_output(_TitleOutput)

    async def answer(self, state: AgentState) -> AgentState:
        """검색 결과를 종합해 answer(+title)을 채운 AgentState를 반환한다."""
        results = self._collect_results(state)
        results_json = json.dumps(results, ensure_ascii=False, default=str)

        answer_out: _AnswerOutput = await self._answer_chain.ainvoke(
            {
                "message": state["message"],
                "results_json": results_json,
            }
        )

        updates: dict = {"answer": answer_out.answer}

        if state.get("title_needed"):
            title_out: _TitleOutput = await self._title_chain.ainvoke(
                {"message": state["message"]}
            )
            updates["title"] = title_out.title

        return {**state, **updates}

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _collect_results(self, state: AgentState) -> list[dict]:
        """검색 결과를 단일 목록으로 합친다.

        우선순위:
          1. hydrated_services  — HydrationNode 가 채운 통합 슬롯 (정식 경로)
          2. sql_results / vector_results — 통합 슬롯 미설정 시 호환 폴백
             (cache hit envelope 또는 단위 테스트에서 HydrationNode 없이 호출되는 경우)
          3. map_results        — GeoJSON 구조라 별도로 unpack

        HydrationNode 가 그래프에 정상 삽입된 정식 경로에서는 항상 (1)로 처리된다.
        """
        raw: list[dict] = []

        hydrated = state.get("hydrated_services")
        if hydrated:
            raw.extend(hydrated)
        else:
            # 폴백 — hydrated_services 슬롯이 비었을 때만 검색 경로별 슬롯에서 채집.
            if state.get("sql_results"):
                raw.extend(state["sql_results"])
            if state.get("vector_results"):
                raw.extend(state["vector_results"])

        if state.get("map_results"):
            # map_results는 GeoJSON dict — features 배열 언팩
            features = state["map_results"].get("features", [])
            raw.extend(f.get("properties", {}) for f in features)

        return [self._normalize(r) for r in raw]

    @staticmethod
    def _normalize(row: dict) -> dict:
        """카드 렌더링에 필요한 필드를 추출하고 fallback URL을 보정한다.

        sql_results와 vector_results는 모두 public_service_reservations 원본 컬럼을
        평탄 dict로 가지므로 metadata 언팩 분기는 더 이상 필요하지 않다.
        map_results는 GeoJSON Feature의 properties dict를 그대로 받는다.

        프롬프트(`_ANSWER_SYSTEM`)가 사용하는 모든 필드를 포함시킨다 —
        max_class_name/min_class_name(분류), payment_type(요금), target_info(대상),
        service_open_*_dt(이용 기간)까지 LLM 컨텍스트에 노출하여 풍부한 답변을 유도.
        """
        service_url = row.get("service_url") or _FALLBACK_URL

        return {
            "service_id": row.get("service_id"),
            "service_name": row.get("service_name"),
            "area_name": row.get("area_name"),
            "place_name": row.get("place_name"),
            "max_class_name": row.get("max_class_name"),
            "min_class_name": row.get("min_class_name"),
            "service_status": row.get("service_status"),
            "payment_type": row.get("payment_type"),
            "target_info": row.get("target_info"),
            "receipt_start_dt": row.get("receipt_start_dt"),
            "receipt_end_dt": row.get("receipt_end_dt"),
            "service_open_start_dt": row.get("service_open_start_dt"),
            "service_open_end_dt": row.get("service_open_end_dt"),
            "service_url": service_url,
        }
