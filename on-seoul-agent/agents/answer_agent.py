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
검색 결과(JSON 배열)를 사용자에게 친절한 한국어로 안내하세요.

# 출력 구조

1) 도입문 (1~2 문장)
   - 예: "테니스장 예약 관련해서 아래 시설을 찾아봤어요." 또는
         "현재 예약 가능한 풋살장을 정리해드릴게요."
   - 결과가 0건이면 "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다." 만 출력.

2) 시설 카드 목록 (상위 5건만, 6건 이상이면 끝에 "외 N건" 표기)

3) 마무리 안내
   - 결과 중 service_status="접수중" 시설이 하나라도 있으면 아래 안내 문구를 그대로 포함:
     "현재 접수중인 시설은 위 '바로가기' 링크에서 예약 내용을 확인하실 수 있습니다.
      인터넷 예약의 경우 시설예약 최초 이용자는 서울시 통합회원 가입이 필요하고,
      가입 시 휴대폰 본인확인 서비스로 본인 인증을 진행해야 합니다."
   - 자치구가 결과에 다양하게 섞여 있거나 사용자가 지역을 명시하지 않은 경우 추가:
     "특정 자치구(예: 강남구, 마포구)나 요금 조건(무료/유료)을 함께 알려주시면 더 정확하게 찾아드릴 수 있어요."

# 카드 형식 (실제 값으로 치환해서 출력, 중괄호 문법 금지)

형식 예시 (값이 비어 있는 줄은 생략):

  • 서남센터 테니스장2번 (강서구 서남물재생센터)
      - 분류: 체육시설 > 테니스장
      - 요금: 유료 / 대상: 어르신
      - 접수 상태: 접수중 (2025-11-01 ~ 2025-12-31)
      - 바로가기: https://yeyak.seoul.go.kr/web/reservation/selectReservView.do?rsv_svc_id=...

# 출력 규칙

- 답변에 중괄호 기호나 JSON 입력의 키 이름(예: service_name, area_name)을 그대로 노출하지 마세요.
  반드시 해당 필드의 실제 값으로 치환해서 출력합니다.
- 각 카드의 바로가기 URL은 시설별 고유 service_url 값을 그대로 사용합니다.
  service_url 이 비어 있는 시설만 https://yeyak.seoul.go.kr 로 표기합니다.
  모든 시설을 yeyak.seoul.go.kr 로 일괄 안내하는 것은 금지입니다.
- service_open_start_dt / service_open_end_dt (이용 기간) 는 답변에 포함하지 마세요.
- 날짜는 'YYYY-MM-DD' 형태로만 표시 (시간 부분 생략).
- 마크다운 헤더(#, ##)나 코드 블록은 사용하지 말고, 자연스러운 줄바꿈으로 가독성을 유지하세요.
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
        if hydrated is not None:
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

        프롬프트(`_ANSWER_SYSTEM`)에서 실제로 출력하는 필드만 LLM 컨텍스트에 노출한다.
        service_open_*_dt(이용 기간) 는 사용자에게 혼란을 주는 비현실적 값이 많아
        의도적으로 제외 (예: 2021-01-01 ~ 2031-12-30 등).
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
            "service_url": service_url,
        }
