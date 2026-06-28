"""Router Agent — 사용자 의도 분류.

LCEL 체인으로 사용자 메시지를 분석해 IntentType 5종 중 하나로 분류한다.
  - SQL_SEARCH  : 카테고리·지역·날짜·상태 등 정형 조건 기반 조회
  - VECTOR_SEARCH: 의미 기반(유사도) 검색
  - ANALYTICS : 집계·분포·종류 요약 ("몇 개", "어디에 많아", "어떤 유형")
  - MAP         : 지도·위치·반경 탐색
  - FALLBACK    : 위 세 가지에 해당하지 않는 일반 안내

history(직전 N턴 대화 이력)가 주어지면 system prompt에 컨텍스트 블록을
append하여 follow-up 질의("성동구는?")가 직전 발화의 카테고리·지역을 이어받도록
유도한다. 빈 리스트/None이면 토큰 절약을 위해 섹션 자체를 생략한다.
"""

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from agents._intake_indexing import neutralize_fence
from llm.client import get_chat_model
from llm.prompts.router import ROUTER_FEW_SHOT, ROUTER_SYSTEM
from schemas.state import IntentType

# Router가 산출하는 post-filter 허용 enum.
# 자유 텍스트가 들어오면 None으로 강제 대체하여 검색 도구 호출 안정성을 보장한다.
_ALLOWED_MAX_CLASS_NAMES: frozenset[str] = frozenset(
    ["체육시설", "문화체험", "공간시설", "교육강좌", "진료복지"]
)
_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(
    ["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"]
)
# payment_type 정규값 — 원천 데이터의 "유료(요금안내문의)" 변형은 sql_search 매칭에서
# 접두("유료%")로 처리하므로, Router 산출값은 "무료"/"유료" 두 정규값만 허용한다.
# DB distinct 확인(2026-06-03): {"유료","무료","유료(요금안내문의)"} — "유료%" 접두가
# 두 유료 변형을 모두 포괄함을 검증함.
_ALLOWED_PAYMENT_TYPES: frozenset[str] = frozenset({"무료", "유료"})

# 서울특별시 25개 자치구 공식 명칭 화이트리스트.
# LLM이 "강남" / "강 남구" / "Gangnam" 등 비표준 형식을 반환하면
# cache key 오염·SQL 빈 결과를 방지하기 위해 None으로 정규화한다.
SEOUL_DISTRICTS: frozenset[str] = frozenset(
    [
        "강남구",
        "강동구",
        "강북구",
        "강서구",
        "관악구",
        "광진구",
        "구로구",
        "금천구",
        "노원구",
        "도봉구",
        "동대문구",
        "동작구",
        "마포구",
        "서대문구",
        "서초구",
        "성동구",
        "성북구",
        "송파구",
        "양천구",
        "영등포구",
        "용산구",
        "은평구",
        "종로구",
        "중구",
        "중랑구",
    ]
)


class _IntentOutput(BaseModel):
    # CoT — LLM이 의도 분류·필터 매핑 근거를 먼저 정리한 뒤 나머지 필드를 채운다.
    # 검색 쿼리에는 사용하지 않고 디버깅·관측용으로만 보관.
    reasoning: str | None = Field(
        default=None,
        description="의도 분류와 필터 매핑 근거 (CoT 사고 정리, 검색 로직 미사용)",
    )
    intent: IntentType
    refined_query: str | None = None
    # Post-filter — SQL_SEARCH / VECTOR_SEARCH 경로에서만 의미가 있다.
    # LLM이 enum을 벗어난 값을 반환하면 검색 도구의 SQL 파라미터로 흘러갈 수 있으므로
    # field_validator에서 None으로 정규화하여 도메인 안전성을 보장한다.
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    # 결제 유형 post-filter — "무료"/"유료" 정규값 또는 None.
    payment_type: str | None = None
    # VECTOR_SEARCH 전용 서브 의도 — RRF 가중치 프로파일 선택에 사용.
    # intent가 VECTOR_SEARCH가 아니면 None. 허용 값 외 → None으로 정규화.
    vector_sub_intent: Literal["identification", "detail", "semantic"] | None = None
    # SQL↔VECTOR 경계가 모호할 때 팬아웃용 보조 의도. SQL_SEARCH/VECTOR_SEARCH만 허용.
    # 그 외(MAP/ANALYTICS/FALLBACK)는 None으로 정규화. None이면 단일 라우트(기존 동작).
    secondary_intent: IntentType | None = None

    @field_validator("secondary_intent", mode="before")
    @classmethod
    def _validate_secondary_intent(cls, v: object) -> IntentType | None:
        """secondary_intent는 SQL_SEARCH 또는 VECTOR_SEARCH만 허용한다."""
        if v is None:
            return None
        allowed = {IntentType.SQL_SEARCH, IntentType.VECTOR_SEARCH}
        if isinstance(v, IntentType):
            return v if v in allowed else None
        if isinstance(v, str) and v in {i.value for i in allowed}:
            return IntentType(v)
        return None

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

    @field_validator("payment_type", mode="before")
    @classmethod
    def _validate_payment_type(cls, v: object) -> str | None:
        """LLM 출력을 "무료"/"유료" 정규값으로 강제한다.

        - 무료/free/공짜 류 → "무료"
        - 유료/paid/요금 류 (원천값 "유료(요금안내문의)" 포함) → "유료"
        - 그 외 / 추출 실패 → None (필터 미적용)
        """
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        normalized = v.strip().lower()
        if not normalized:
            return None
        if "무료" in normalized or "free" in normalized or "공짜" in normalized:
            return "무료"
        if "유료" in normalized or "paid" in normalized or "요금" in normalized:
            return "유료"
        return None

    @field_validator("vector_sub_intent", mode="before")
    @classmethod
    def _validate_vector_sub_intent(cls, v: object) -> str | None:
        if v is None:
            return None
        if v in {"identification", "detail", "semantic"}:
            return v  # type: ignore[return-value]
        return None


def build_context_block(history: list[dict[str, str]] | None) -> str:
    """history(직전 N턴)를 system prompt에 append할 블록으로 변환.

    비어 있으면 빈 문자열을 반환하여 섹션 자체를 생략한다(토큰 절약).

    프롬프트 인젝션 표면: history.content(사용자·어시스턴트 발화)는 외부 입력이며
    escape 없이 system prompt에 삽입된다. 이 블록의 소비자는 Router/Triage(intake)의
    structured output뿐 아니라 clarify/explain 같은 자유 텍스트 답변 생성
    노드까지 포함하므로, content가 임의 지시를 담으면 경계를 탈출해 주입으로 이어질 수
    있다. 따라서 각 turn content의 fence 마커(---..._START---/---..._END---)를
    neutralize_fence로 중화해 위조 경계 마커를 무력화한다. 일반 대시·하이픈은 보존된다.
    (content는 HistoryTurn max_length=1000 + API 서비스 10메시지 윈도우로 길이도 제한.)
    """
    if not history:
        return ""
    lines = []
    for turn in history:
        role_label = "사용자" if turn["role"] == "user" else "어시스턴트"
        content = neutralize_fence(turn["content"])
        lines.append(f"- [{role_label}] {content}")
    turns_text = "\n".join(lines)
    return (
        "이전 대화 이력 (과거 → 최신). 후속 질의는 직전 발화의 "
        "카테고리·지역을 이어받을 가능성이 높다.\n"
        "이전 맥락이 명확하면 refined_query에 카테고리·지역 키워드를 병합한다.\n"
        f"{turns_text}"
    )


class RouterAgent:
    """LCEL 기반 의도 분류 에이전트.

    LLM의 with_structured_output으로 IntentType을 직접 추출한다.
    history는 호출마다 system prompt에 동적으로 합성된다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()

    def _build_context_block(self, history: list[dict[str, str]] | None) -> str:
        """모듈 수준 build_context_block 위임 (하위호환)."""
        return build_context_block(history)

    async def classify(
        self,
        message: str,
        history: list[dict[str, str]] | None = None,
    ) -> _IntentOutput:
        """사용자 메시지의 의도를 분류해 _IntentOutput을 반환한다.

        Args:
            message: 사용자 원본 발화.
            history: 직전 N턴 대화 이력(과거→최신). 기본값 None.
                비어 있으면 system prompt에 컨텍스트 섹션을 추가하지 않는다.
        """
        # OpenAI 자동 프롬프트 캐싱(1024토큰↑ 프리픽스): 정적 ROUTER_SYSTEM +
        # ROUTER_FEW_SHOT 를 프롬프트 맨 앞 고정 프리픽스로 두고, 동적 history/message 는
        # 그 *뒤*에 붙인다. history 를 SystemMessage 에 합성하면 정적 few-shot 블록 앞에
        # 동적 텍스트가 끼어 프리픽스가 깨지므로, history 는 별도 SystemMessage 로 분리해
        # few-shot 이후·user 메시지 이전에 위치시킨다.
        context_block = self._build_context_block(history)
        messages: list = [
            SystemMessage(content=ROUTER_SYSTEM),
            *ROUTER_FEW_SHOT.format_messages(),
        ]
        if context_block:
            messages.append(SystemMessage(content=context_block))
        messages.append(HumanMessage(content=f"사용자 메시지: {message}"))
        structured = self._llm.with_structured_output(_IntentOutput)
        result: _IntentOutput = await structured.ainvoke(messages)
        return result
