"""Router Agent — 사용자 의도 분류.

LCEL 체인으로 사용자 메시지를 분석해 IntentType 4종 중 하나로 분류한다.
  - SQL_SEARCH  : 카테고리·지역·날짜·상태 등 정형 조건 기반 조회
  - VECTOR_SEARCH: 의미 기반(유사도) 검색
  - MAP         : 지도·위치·반경 탐색
  - FALLBACK    : 위 세 가지에 해당하지 않는 일반 안내

recent_queries(per-room 최근 발화)가 주어지면 system prompt에 컨텍스트 블록을
append하여 follow-up 질의("성동구는?")가 직전 발화의 카테고리·지역을 이어받도록
유도한다. 빈 리스트/None이면 토큰 절약을 위해 섹션 자체를 생략한다.
"""

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from pydantic import BaseModel, field_validator

from core.config import settings
from llm.client import get_chat_model
from schemas.state import IntentType

# Router가 산출하는 post-filter 허용 enum.
# 자유 텍스트가 들어오면 None으로 강제 대체하여 검색 도구 호출 안정성을 보장한다.
_ALLOWED_MAX_CLASS_NAMES: frozenset[str] = frozenset(
    ["체육시설", "문화행사", "시설대관", "교육", "진료"]
)
_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(
    ["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"]
)

# 서울특별시 25개 자치구 공식 명칭 화이트리스트.
# LLM이 "강남" / "강 남구" / "Gangnam" 등 비표준 형식을 반환하면
# cache key 오염·SQL 빈 결과를 방지하기 위해 None으로 정규화한다.
SEOUL_DISTRICTS: frozenset[str] = frozenset([
    "강남구", "강동구", "강북구", "강서구", "관악구",
    "광진구", "구로구", "금천구", "노원구", "도봉구",
    "동대문구", "동작구", "마포구", "서대문구", "서초구",
    "성동구", "성북구", "송파구", "양천구", "영등포구",
    "용산구", "은평구", "종로구", "중구", "중랑구",
])

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

intent 분류 외에, 검색에 사용할 'refined_query'를 함께 산출하라.
- SQL_SEARCH / VECTOR_SEARCH: 사용자 발화를 검색 친화적 단문으로 정제 (카테고리·지역 키워드 포함, 군더더기 제거)
- 직전 맥락이 주어지면 카테고리/지역을 이어받아 병합한다
- MAP / FALLBACK: refined_query는 null로 두어도 좋다

SQL_SEARCH / VECTOR_SEARCH일 때 가능하면 아래 post-filter 메타데이터도 함께 산출하라.
명시되지 않으면 반드시 null로 반환한다 (자유 텍스트·추측 금지).
- max_class_name: 체육시설·문화행사·시설대관·교육·진료 중 하나
- area_name: 서울 자치구 이름 (예: 강남구, 마포구)
- service_status: 접수중·예약마감·접수종료·예약일시중지·안내중 중 하나
직전 맥락의 카테고리·지역은 후속 발화에 이어받아 채워도 좋다.

intent가 VECTOR_SEARCH인 경우 vector_sub_intent를 다음 3종 중 하나로 분류하세요.

- identification: 시설명/지역/분류 식별 (예: "마포구 풋살장", "응봉공원 테니스장")
- detail: 요금/취소/시간 등 세부정보 (예: "테니스장 평일 이용료", "취소 며칠 전까지")
- semantic: 활동/체험/맥락 의미 (예: "아이랑 갈 만한 무료 체험", "드론 날릴 수 있는 곳")

intent가 VECTOR_SEARCH가 아니면 vector_sub_intent는 null로 두세요.
"""


# ---------------------------------------------------------------------------
# Few-shot 예시 — 의도 분류 정확도 향상
#
# 3개 예시가 커버하는 경계:
#   1. SQL_SEARCH  — 접수상태·지역 같은 명시적 조건이 있으면 SQL (VECTOR와 경계)
#   2. VECTOR/identification — 시설명·지역 조합으로 특정 시설을 찾을 때
#   3. VECTOR/semantic       — 활동·경험·맥락 기반 탐색 (vector_sub_intent 가중치 최대)
# ---------------------------------------------------------------------------
_FEW_SHOT_EXAMPLES = [
    {
        "message": "마포구 문화행사 이번 주 접수 중인 거 보여줘",
        "output": (
            '{"intent": "SQL_SEARCH",'
            ' "refined_query": "마포구 접수중 문화행사",'
            ' "max_class_name": "문화행사", "area_name": "마포구",'
            ' "service_status": "접수중", "vector_sub_intent": null}'
        ),
    },
    {
        "message": "응봉공원 테니스장 예약하고 싶어",
        "output": (
            '{"intent": "VECTOR_SEARCH",'
            ' "refined_query": "응봉공원 테니스장",'
            ' "max_class_name": null, "area_name": null,'
            ' "service_status": null, "vector_sub_intent": "identification"}'
        ),
    },
    {
        "message": "주말에 아이랑 같이 즐길 수 있는 무료 체험 프로그램",
        "output": (
            '{"intent": "VECTOR_SEARCH",'
            ' "refined_query": "아동 참여 무료 주말 체험 프로그램",'
            ' "max_class_name": null, "area_name": null,'
            ' "service_status": null, "vector_sub_intent": "semantic"}'
        ),
    },
    {
        "message": "수영장 평일 오전 이용 요금이랑 취소 규정 알려줘",
        "output": (
            '{"intent": "VECTOR_SEARCH",'
            ' "refined_query": "수영장 평일 이용 요금 취소 규정",'
            ' "max_class_name": null, "area_name": null,'
            ' "service_status": null, "vector_sub_intent": "detail"}'
        ),
    },
]

_FEW_SHOT: FewShotChatMessagePromptTemplate = FewShotChatMessagePromptTemplate(
    example_prompt=ChatPromptTemplate.from_messages([
        ("human", "사용자 메시지: {message}"),
        ("ai", "{output}"),
    ]),
    examples=_FEW_SHOT_EXAMPLES,
)


class _IntentOutput(BaseModel):
    intent: IntentType
    refined_query: str | None = None
    # Post-filter — SQL_SEARCH / VECTOR_SEARCH 경로에서만 의미가 있다.
    # LLM이 enum을 벗어난 값을 반환하면 검색 도구의 SQL 파라미터로 흘러갈 수 있으므로
    # field_validator에서 None으로 정규화하여 도메인 안전성을 보장한다.
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    # VECTOR_SEARCH 전용 서브 의도 — RRF 가중치 프로파일 선택에 사용.
    # intent가 VECTOR_SEARCH가 아니면 None. 허용 값 외 → None으로 정규화.
    vector_sub_intent: Literal["identification", "detail", "semantic"] | None = None

    @field_validator("max_class_name", mode="before")
    @classmethod
    def _validate_max_class_name(cls, v: object) -> str | None:
        if v is None:
            return None
        if v in _ALLOWED_MAX_CLASS_NAMES:
            return v  # type: ignore[return-value]
        return None

    @field_validator("area_name", mode="before")
    @classmethod
    def _validate_area_name(cls, v: object) -> str | None:
        if v is None:
            return None
        if v in SEOUL_DISTRICTS:
            return v  # type: ignore[return-value]
        return None

    @field_validator("service_status", mode="before")
    @classmethod
    def _validate_service_status(cls, v: object) -> str | None:
        if v is None:
            return None
        if v in _ALLOWED_SERVICE_STATUSES:
            return v  # type: ignore[return-value]
        return None

    @field_validator("vector_sub_intent", mode="before")
    @classmethod
    def _validate_vector_sub_intent(cls, v: object) -> str | None:
        if v is None:
            return None
        if v in {"identification", "detail", "semantic"}:
            return v  # type: ignore[return-value]
        return None


class RouterAgent:
    """LCEL 기반 의도 분류 에이전트.

    LLM의 with_structured_output으로 IntentType을 직접 추출한다.
    recent_queries는 호출마다 system prompt에 동적으로 합성된다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()

    def _build_context_block(self, recent_queries: list[str] | None) -> str:
        """recent_queries를 system prompt에 append할 블록으로 변환.

        비어 있으면 빈 문자열을 반환하여 섹션 자체를 생략한다(토큰 절약).
        보관/주입 개수는 settings.recent_queries_max로 통일한다.
        """
        if not recent_queries:
            return ""
        lines = "\n".join(
            f"- {q}" for q in recent_queries[: settings.recent_queries_max]
        )
        return (
            "이전 사용자 발화 (최신 순). 후속 질의는 직전 발화의 "
            "카테고리·지역을 이어받을 가능성이 높다.\n"
            "이전 맥락이 명확하면 refined_query에 카테고리·지역 키워드를 병합한다.\n"
            f"{lines}"
        )

    async def classify(
        self,
        message: str,
        recent_queries: list[str] | None = None,
    ) -> _IntentOutput:
        """사용자 메시지의 의도를 분류해 _IntentOutput을 반환한다.

        Args:
            message: 사용자 원본 발화.
            recent_queries: per-room 최근 발화(최신 순). 기본값 None.
                비어 있으면 system prompt에 컨텍스트 섹션을 추가하지 않는다.
        """
        context_block = self._build_context_block(recent_queries)
        system_text = _SYSTEM + (f"\n\n{context_block}" if context_block else "")
        messages = [
            SystemMessage(content=system_text),
            *_FEW_SHOT.format_messages(),
            HumanMessage(content=f"사용자 메시지: {message}"),
        ]
        structured = self._llm.with_structured_output(_IntentOutput)
        result: _IntentOutput = await structured.ainvoke(messages)
        return result
