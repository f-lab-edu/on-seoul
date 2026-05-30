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

2) 시설 카드 목록 (전달된 결과 전체를 상세 안내. "추가 미표시 건수"가 0보다 크면 카드 목록 끝에 "외 N건" 표기)

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
- 날짜는 'YYYY-MM-DD' 형태로만 표시 (시간 부분 생략).
- 마크다운 헤더(#, ##)나 코드 블록은 사용하지 말고, 자연스러운 줄바꿈으로 가독성을 유지하세요.
"""

_ANSWER_HUMAN = """\
사용자 질문: {message}

검색 결과:
{results_json}

추가 미표시 건수: {extra_count}
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

# 카드 상세 표시 상한. 이 값 초과분의 건수(extra_count)만 숫자로 LLM에 전달된다.
# 클래스 밖 모듈 상수로 두어 인스턴스 오버라이드로 프롬프트와 불일치하는 사고를 방지한다.
_DISPLAY_LIMIT: int = 5


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
        """검색 결과를 종합해 answer(+title)을 채운 AgentState를 반환한다.

        상위 `_DISPLAY_LIMIT`건만 LLM에 전달하고, 나머지 건수는 `extra_count`로
        별도 전달한다. LLM 토큰 절약 + "외 N건" 비즈니스 규칙의 코드 수준 강제.

        상위 `_DISPLAY_LIMIT` 건을 `service_cards` 슬롯에도 노출한다 —
        LLM 자연어 답변 파싱 없이 프론트 카드 UI 가 직접 구조화 결과를 사용한다.
        빈 결과여도 `[]` 로 명시 설정하여 None(미실행) 과 구별한다.
        """
        all_results = self._collect_results(state)
        display = all_results[:_DISPLAY_LIMIT]
        extra_count = max(0, len(all_results) - _DISPLAY_LIMIT)
        results_json = json.dumps(display, ensure_ascii=False, default=str)

        answer_out: _AnswerOutput = await self._answer_chain.ainvoke(
            {
                "message": state["message"],
                "results_json": results_json,
                "extra_count": extra_count,
            }
        )

        # service_cards 슬롯에는 shallow copy 로 분리한다.
        # display 리스트는 LLM 입력(results_json) 직렬화에 이미 사용된 동일 참조이며,
        # 향후 LLM 전처리 단계가 추가되어 inplace mutate 될 경우 외부 노출 경로
        # (SSE final payload, cache envelope) 가 오염될 수 있다. 최대 5건 × 12 필드라
        # 복사 비용은 무시 가능.
        updates: dict = {
            "answer": answer_out.answer,
            "service_cards": [dict(card) for card in display],
        }

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

        ## 의도적 제외 필드: service_open_start_dt / service_open_end_dt (이용 기간)

        DB(`public_service_reservations`) 의 운영 기간 컬럼에 신뢰할 수 없는 값이
        다수 존재한다 (예: 2021-01-01 ~ 2031-12-30 처럼 10년에 걸친 비현실적 범위).
        사용자가 답변에서 이 값을 보면 혼란을 유발하므로 LLM 컨텍스트에서 아예
        제외한다. 결과적으로:
          - `_normalize()` 반환 dict 에 두 필드를 **포함하지 않는다** (현재 구현).
          - 프롬프트(`_ANSWER_SYSTEM`)의 카드 형식 예시에도 이용 기간 줄이 **없다**.
          - 데이터 신뢰성이 개선되면(별도 작업) 다시 노출 검토.
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
