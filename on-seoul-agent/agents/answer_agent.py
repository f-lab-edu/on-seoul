"""Answer Agent — 자연어 답변 + 시설 카드 가공.

AgentState의 검색 결과(sql_results / vector_results / map_results)를 종합해
사용자에게 전달할 최종 답변과 시설 카드 목록을 생성한다.

title_needed=True 인 경우(첫 메시지) 대화 제목도 함께 생성한다.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
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

상세 정보(분류·요금·접수 상태·바로가기 링크)는 답변과 별도로 제공되는
시설 카드 UI 가 보여줍니다. 따라서 답변 본문에서는 상세를 반복하지 말고
시설명만 간결히 나열하세요.

# 출력 구조

1) 도입문 (1~2 문장)
   - 매번 똑같은 문장을 반복하지 말고, 질문 맥락(시설 종류·지역·요금 등)에
     맞춰 자연스럽게 변형하세요. 아래는 톤 참고용 예시일 뿐 그대로 복사하지 마세요:
       · "테니스장 예약 관련해서 아래 시설을 찾아봤어요."
       · "광진구에서 이용할 수 있는 풋살장을 정리해드릴게요."
       · "말씀하신 조건에 맞는 시설이 몇 곳 있네요. 아래에서 확인해보세요."
       · "지금 접수 중인 수영장 위주로 골라봤어요."
   - 결과가 0건이면 "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다." 만 출력.

2) 시설명 목록 (전달된 결과의 시설명만 한 줄씩 나열. 상세 줄은 출력 금지.
   "추가 미표시 건수"가 0보다 크면 목록 끝에 "외 N건" 표기)

3) 마무리 안내
   - 결과 중 service_status="접수중" 시설이 하나라도 있으면 아래 안내 문구를 그대로 포함:
     "현재 접수중인 시설은 카드의 '바로가기' 링크를 통해 예약 내용을 확인하실 수 있습니다.
      인터넷 예약의 경우 시설예약 최초 이용자는 서울시 통합회원 가입이 필요하고,
      가입 시 휴대폰 본인확인 서비스로 본인 인증을 진행해야 합니다."
   - 아래 안내는 **사용자 질문에 자치구가 명시되지 않았을 때에만** 추가:
     의미는 유지하되 매번 같은 문장이 되지 않게 자연스럽게 변형하세요. 톤 참고용 예시:
       · "특정 자치구(예: 강남구, 마포구)나 요금 조건(무료/유료)을 함께 알려주시면 더 정확하게 찾아드릴 수 있어요."
       · "원하시는 지역이나 무료/유료 여부를 알려주시면 더 좁혀서 찾아드릴게요."
     사용자 질문에 이미 자치구(예: "광진구 풋살장")가 들어 있으면 이 문구를 출력하지 마세요.

# 시설명 목록 형식 (실제 값으로 치환해서 출력, 중괄호 문법 금지)

형식 예시:

  • 서남센터 테니스장2번 (강서구)
  • 마포구민체육센터 테니스장 (마포구)

   - 시설명 뒤 괄호에는 자치구(area_name)만 간단히 표기합니다.
   - 분류·요금·대상·접수 상태·바로가기 줄은 출력하지 않습니다 (카드 UI 담당).

# 출력 규칙

- 답변에 중괄호 기호나 JSON 입력의 키 이름(예: service_name, area_name)을 그대로 노출하지 마세요.
  반드시 해당 필드의 실제 값으로 치환해서 출력합니다.
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


def _iso_or_none(value):
    """datetime/date 값을 ISO 8601 문자열로 변환한다.

    프론트 계약(chat-service-cards-interface §5)은 receipt_*_dt 가
    "2025-11-01T00:00:00" 형태 ISO 8601 로 직렬화되기를 요구한다.
    sse_frame 의 json.dumps(default=str) 폴백은 str(datetime) → 공백 구분자
    ("2025-11-01 00:00:00") 를 내므로, _normalize 단에서 명시적으로 isoformat()
    하여 'T' 구분자를 보장한다. (default=str 은 다른 타입 방어용으로 유지)

    이미 str 이거나 None 이면 그대로 통과한다.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


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
        # answer는 자유형 장문 텍스트다. 구조화 출력(JSON {"answer": ...})으로 감싸면
        # 모델이 평문을 그대로 반환할 때 JSON 파서가 실패한다(OutputParserException).
        # 단일 문자열이므로 StrOutputParser로 직접 받아 이 실패 모드를 제거한다.
        self._answer_chain = answer_prompt | llm | StrOutputParser()

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

        answer_text: str = await self._answer_chain.ainvoke(
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
            "answer": answer_text,
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
        # service_url 스킴 가드: http(s):// 로 시작하지 않으면(빈 값/None 포함)
        # fallback URL 로 강등한다. DB 원본을 무검증 통과시키면 프론트가 href 에
        # 그대로 링크하므로 javascript:/data: 등 위험 스킴을 차단해야 한다.
        url = row.get("service_url")
        if not url or not str(url).startswith(("http://", "https://")):
            url = _FALLBACK_URL

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
            "receipt_start_dt": _iso_or_none(row.get("receipt_start_dt")),
            "receipt_end_dt": _iso_or_none(row.get("receipt_end_dt")),
            "service_url": url,
        }
