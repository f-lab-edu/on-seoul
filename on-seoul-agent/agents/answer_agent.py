"""Answer Agent — 자연어 답변 + 시설 카드 가공.

AgentState의 검색 결과(sql_results / vector_results / map_results)를 종합해
사용자에게 전달할 최종 답변과 시설 카드 목록을 생성한다.

title_needed=True 인 경우(첫 메시지) 대화 제목도 함께 생성한다.

## 프롬프트 조립 구조 (2-Tier)

Tier 1 — __init__ 1회 조립 (MAP / ANALYTICS / FALLBACK):
  조건부 절이 없으므로 self._static_prompts dict에 완전 캐시.

Tier 2 — 런타임 조립 (SQL_SEARCH / VECTOR_SEARCH):
  _build_card_system(message, results) 가 호출마다 조건부 절을 평가하여 조립.
  조건: "접수중" 시설 존재 여부, 사용자 질문 내 자치구 명시 여부.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from agents.router_agent import SEOUL_DISTRICTS
from llm.client import get_chat_model
from schemas.state import AgentState, IntentType

# ---------------------------------------------------------------------------
# 프롬프트 컴포넌트 (모듈 레벨 상수)
# ---------------------------------------------------------------------------

_ROLE = "당신은 서울시 공공서비스 예약 안내 챗봇입니다."

_OUTPUT_RULES = """\
# 출력 규칙

- 답변에 중괄호 기호나 JSON 입력의 키 이름(예: service_name, area_name)을 그대로 노출하지 마세요.
  반드시 해당 필드의 실제 값으로 치환해서 출력합니다.
- 마크다운 헤더(#, ##)나 코드 블록은 사용하지 말고, 자연스러운 줄바꿈으로 가독성을 유지하세요."""

_STRUCT_CARD_LIST = """\
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

3) 마무리 안내 (아래 _CLAUSE_RESERVATION_GUIDE / _CLAUSE_REFINE_HINT 절 참조)

# 시설명 목록 형식 (실제 값으로 치환해서 출력, 중괄호 문법 금지)

형식 예시:

  • 서남센터 테니스장2번 (강서구)
  • 마포구민체육센터 테니스장 (마포구)

   - 시설명 뒤 괄호에는 자치구(area_name)만 간단히 표기합니다.
   - 분류·요금·대상·접수 상태·바로가기 줄은 출력하지 않습니다 (카드 UI 담당)."""

_STRUCT_MAP = """\
검색 결과(GeoJSON properties 배열)를 사용자에게 친절한 한국어로 안내하세요.
distance_m(미터) 값이 있으면 가장 가까운 거리를 자연스럽게 강조하세요.

# 출력 구조

1) "내 주변 N곳을 찾았어요." 형태의 도입문 (결과 건수 포함)
   - 결과가 0건이면 "주변에서 조건에 맞는 시설을 찾지 못했습니다." 만 출력.

2) 시설명 목록 (가까운 순. 시설명 + 자치구 + 거리(km 또는 m) 한 줄씩)

3) 마무리: 지도 카드에서 정확한 위치를 확인하도록 안내."""

_STRUCT_ANALYTICS = """\
집계 결과(JSON 배열)를 사용자에게 친절한 한국어로 요약하세요.
각 행은 group_value(항목명)과 count(건수) 또는 서비스 목록을 포함합니다.

# 출력 구조

1) 요약 도입문 (1~2 문장, 집계 차원·결과 수 언급)
   - 결과가 0건이면 "조건에 맞는 집계 결과를 찾지 못했습니다." 만 출력.

2) 순위/개수 요약 (상위 항목 위주로 간결하게, 시설명 개별 나열 금지)

3) 마무리: 특정 카테고리나 지역을 지정하면 더 자세히 안내할 수 있다는 안내."""

_STRUCT_FALLBACK = """\
사용자 질문이 공공서비스 예약 조회 범위에 해당하지 않거나 검색 결과가 없습니다.
다음과 같은 기능을 안내하세요.

# 안내 내용

1) 이용 가능한 기능 소개 (카테고리 조회, 지역 탐색, 지도 검색, 집계/통계)
2) 질문 예시 1~2개 (자연스럽게 변형, 그대로 복사 금지):
   · "강남구 테니스장 접수중인 곳 알려줘"
   · "내 주변 수영장 찾아줘"
   · "서울에 체육시설이 가장 많은 구는?"
3) 다시 질문해달라는 마무리 안내."""

_CLAUSE_RESERVATION_GUIDE = """\
현재 접수중인 시설은 카드의 '바로가기' 링크를 통해 예약 내용을 확인하실 수 있습니다.
인터넷 예약의 경우 시설예약 최초 이용자는 서울시 통합회원 가입이 필요하고,
가입 시 휴대폰 본인확인 서비스로 본인 인증을 진행해야 합니다."""

_CLAUSE_REFINE_HINT = """\
특정 자치구(예: 강남구, 마포구)나 요금 조건(무료/유료)을 함께 알려주시면 더 정확하게 찾아드릴 수 있어요.
원하시는 지역이나 무료/유료 여부를 알려주시면 더 좁혀서 찾아드릴게요."""


def _compose(*blocks: str) -> str:
    """비어있지 않은 블록들을 빈 줄로 연결한다."""
    return "\n\n".join(b.strip() for b in blocks if b.strip())


def _has_district_in_message(message: str) -> bool:
    """사용자 메시지에 서울 25개 자치구 공식 명칭이 포함되어 있는지 반환한다.

    SEOUL_DISTRICTS(공식 명칭 화이트리스트)만 인정하며, "강남" 같은 비공식 표기는
    false를 반환한다. _build_card_system에서 _CLAUSE_REFINE_HINT 절 포함 여부를
    결정할 때 사용한다.

    Args:
        message: 사용자 원본 발화 문자열.

    Returns:
        True  — 공식 자치구명이 하나 이상 포함된 경우.
        False — 공식 자치구명이 없거나 비공식 표기("강남")만 포함된 경우.
    """
    return any(district in message for district in SEOUL_DISTRICTS)


def _build_card_system(message: str, results: list[dict]) -> str:
    """카드형(SQL/VECTOR) intent의 시스템 프롬프트를 런타임에 조립한다.

    조건부 절:
    - _CLAUSE_RESERVATION_GUIDE: 결과 중 service_status="접수중" 시설이 있을 때만 추가.
    - _CLAUSE_REFINE_HINT: 사용자 질문에 공식 자치구명이 없을 때만 추가.

    Args:
        message: 사용자 원본 발화 (자치구 명시 여부 판단용).
        results: 정규화 이전 또는 이후 결과 목록 (service_status 키 접근).

    Returns:
        조립된 시스템 프롬프트 문자열.
    """
    blocks = [_ROLE, _STRUCT_CARD_LIST]
    if any(r.get("service_status") == "접수중" for r in results):
        blocks.append(_CLAUSE_RESERVATION_GUIDE)
    if not _has_district_in_message(message):
        blocks.append(_CLAUSE_REFINE_HINT)
    blocks.append(_OUTPUT_RULES)
    return _compose(*blocks)


# ---------------------------------------------------------------------------
# 답변 생성 프롬프트 (인간 메시지 템플릿)
# ---------------------------------------------------------------------------

# 모든 intent 공용 human 템플릿.
# {system}은 intent별로 _compose()가 조립한 시스템 프롬프트를 runtime에 주입받는다.
# ANALYTICS intent도 이 템플릿을 사용하며, extra_count=0을 함께 전달한다.
# (집계 행에는 "추가 미표시 건수: 0" 이 LLM 컨텍스트에 노출되나 결과에 무해)
_ANSWER_HUMAN = """\
사용자 질문: {message}

검색 결과:
{results_json}

추가 미표시 건수: {extra_count}"""

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
    """검색 결과 → 자연어 답변 + 시설 카드 + (선택) 제목 생성 에이전트.

    ## 프롬프트 조립 전략

    __init__에서 MAP/ANALYTICS/FALLBACK 시스템 프롬프트를 self._static_prompts에
    캐시한다(Tier 1). SQL_SEARCH/VECTOR_SEARCH는 answer() 호출 시 _build_card_system이
    조건부 절을 평가하여 조립한다(Tier 2).

    _answer_chain은 단일 체인으로 유지하되, system 메시지를 {system} 변수로
    파라미터화하여 intent별 분기를 answer() 내에서 처리한다. 이 방식은 기존
    단위 테스트가 agent._answer_chain.ainvoke를 mock하는 구조와 완전 호환된다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()

        # system 메시지를 {system} 변수로 파라미터화: intent별 프롬프트를 runtime에 주입.
        # human 메시지는 기존 {message}/{results_json}/{extra_count} 변수를 유지하여
        # 기존 단위 테스트(ainvoke call_args 검사)와의 호환성을 보장한다.
        answer_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "{system}"),
                ("human", _ANSWER_HUMAN),
            ]
        )
        self._answer_chain = answer_prompt | llm | StrOutputParser()

        title_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _TITLE_SYSTEM),
                ("human", _TITLE_HUMAN),
            ]
        )
        self._title_chain = title_prompt | llm.with_structured_output(_TitleOutput)

        # Tier 1: 조건부 절 없는 intent 시스템 프롬프트를 init 1회 조립 후 캐시.
        self._static_prompts: dict[str, str] = {
            IntentType.MAP.value: _compose(_ROLE, _STRUCT_MAP, _OUTPUT_RULES),
            IntentType.ANALYTICS.value: _compose(
                _ROLE, _STRUCT_ANALYTICS, _OUTPUT_RULES
            ),
            IntentType.FALLBACK.value: _compose(_ROLE, _STRUCT_FALLBACK, _OUTPUT_RULES),
        }

    async def answer(self, state: AgentState) -> AgentState:
        """검색 결과를 종합해 answer(+title)을 채운 AgentState를 반환한다.

        intent별 분기:
        - ANALYTICS: analytics_results를 직접 읽어 LLM에 전달. service_cards=[].
        - FALLBACK:  빈 JSON 배열 전달. service_cards=[].
        - MAP:       _collect_results 경로(GeoJSON features 언팩). service_cards 기존 경로.
        - SQL_SEARCH / VECTOR_SEARCH / None: _build_card_system으로 Tier 2 조립.
          상위 _DISPLAY_LIMIT건 슬라이스 + extra_count.
        """
        intent = state.get("intent")
        message = state["message"]

        if intent == IntentType.ANALYTICS:
            # ANALYTICS: analytics_results를 직접 LLM에 전달. _normalize 미경유.
            # extra_count=0을 함께 전달하여 _ANSWER_HUMAN 템플릿 변수 충족.
            # (집계 행은 카드가 아니므로 "추가 미표시 건수: 0"은 LLM 컨텍스트에 무해)
            system_prompt = self._static_prompts[IntentType.ANALYTICS.value]
            raw_analytics = state.get("analytics_results") or []
            results_json = json.dumps(raw_analytics, ensure_ascii=False, default=str)
            answer_text: str = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": results_json,
                    "extra_count": 0,
                }
            )
            updates: dict = {"answer": answer_text, "service_cards": []}

        elif intent == IntentType.FALLBACK:
            system_prompt = self._static_prompts[IntentType.FALLBACK.value]
            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": "[]",
                    "extra_count": 0,
                }
            )
            updates = {"answer": answer_text, "service_cards": []}

        else:
            # MAP, SQL_SEARCH, VECTOR_SEARCH, None
            all_results = self._collect_results(state)
            display = all_results[:_DISPLAY_LIMIT]
            extra_count = max(0, len(all_results) - _DISPLAY_LIMIT)
            results_json = json.dumps(display, ensure_ascii=False, default=str)

            if intent == IntentType.MAP:
                system_prompt = self._static_prompts[IntentType.MAP.value]
            else:
                # Tier 2: 카드형 (SQL_SEARCH / VECTOR_SEARCH / None)
                system_prompt = _build_card_system(message, display)

            answer_text = await self._answer_chain.ainvoke(
                {
                    "system": system_prompt,
                    "message": message,
                    "results_json": results_json,
                    "extra_count": extra_count,
                }
            )

            # service_cards 슬롯에는 shallow copy 로 분리한다.
            # display 리스트는 LLM 입력(results_json) 직렬화에 이미 사용된 동일 참조이며,
            # 향후 LLM 전처리 단계가 추가되어 inplace mutate 될 경우 외부 노출 경로
            # (SSE final payload, cache envelope) 가 오염될 수 있다. 최대 5건 × 12 필드라
            # 복사 비용은 무시 가능.
            updates = {
                "answer": answer_text,
                "service_cards": [dict(card) for card in display],
            }

        if state.get("title_needed"):
            title_out: _TitleOutput = await self._title_chain.ainvoke(
                {"message": message}
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

        ANALYTICS 결과(analytics_results)는 여기서 처리하지 않는다.
        집계 행은 _normalize가 맞지 않으므로 answer()에서 직접 처리한다.

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

        프롬프트에서 실제로 출력하는 필드만 LLM 컨텍스트에 노출한다.

        ## 의도적 제외 필드: service_open_start_dt / service_open_end_dt (이용 기간)

        DB(`public_service_reservations`) 의 운영 기간 컬럼에 신뢰할 수 없는 값이
        다수 존재한다 (예: 2021-01-01 ~ 2031-12-30 처럼 10년에 걸친 비현실적 범위).
        사용자가 답변에서 이 값을 보면 혼란을 유발하므로 LLM 컨텍스트에서 아예
        제외한다. 결과적으로:
          - `_normalize()` 반환 dict 에 두 필드를 **포함하지 않는다** (현재 구현).
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
