"""on-seoul-api(Spring Boot)와의 채팅 API 계약 스키마.

네이밍 근거
- 엔드포인트가 /chat/stream이고 호출 경로가 프론트엔드 → on-seoul-api → on-seoul-agent 이므로
  API 계약은 채팅 맥락을 따른다.
- request.message : 사용자가 채팅창에 입력한 텍스트. chat_messages.content(role=user)에 저장된다.
- response.answer : 에이전트가 생성한 자연어 답변.  chat_messages.content(role=assistant)에 저장된다.
  내부 AgentState.answer와 동일한 개념이며, 요청 필드 message와의 혼동을 피하기 위해 다른 이름을 사용한다.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from schemas.state import IntentType


class HistoryTurn(BaseModel):
    """API 서비스가 chat_messages 테이블에서 조립하여 전달하는 단일 발화 턴.

    role: "user" | "assistant" (소문자. LLM 컨벤션)
    content: 메시지 원문. API 서비스가 최대 1000자로 잘라 전달.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=0, max_length=1000)


class PrevEntity(BaseModel):
    """직전 턴의 결과 엔티티(정체성). API 서비스가 영속 service_cards에서 조립.

    service_id: 직전 답변에 노출된 시설의 식별자.
    label: 사용자에게 노출된 라벨(시설명). 지시 참조의 부분일치 판정에 쓰인다.

    철학(W1): 정체성(service_id·label)만 이어받고, 사실(상태·일정)은 재-hydrate.
    스냅샷 캐싱 금지 — staleness 위험.
    """

    service_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=0, max_length=200)


class ChatRequest(BaseModel):
    room_id: int = Field(ge=1)
    message_id: int = Field(ge=1)
    message: str = Field(
        min_length=1, max_length=2000
    )  # 사용자 채팅 입력. on-seoul-api가 릴레이한다.
    # 지도 검색(MAP intent)용 사용자 위치. 미전송 시 MAP을 FALLBACK으로 대체한다.
    # 범위 제한: 범위 외 값은 ll_to_earth()에서 DB 오류를 유발하므로 422로 차단한다.
    lat: float | None = Field(default=None, ge=-90.0, le=90.0)  # 위도 (latitude)
    lng: float | None = Field(default=None, ge=-180.0, le=180.0)  # 경도 (longitude)
    # 직전 N턴(USER+ASSISTANT 쌍). API 서비스가 chat_messages에서 조립.
    # seq 오름차순(과거→최신). 없으면 빈 배열. null 미전송.
    history: list[HistoryTurn] = Field(default_factory=list)
    # ─── W1: 결과 엔티티 carryover + 참조 해소 ───
    # 직전 턴 산출물(정체성). API 서비스가 영속 service_cards에서 조립해 회신.
    # 미전송 시 빈 배열 → reference_resolution_node가 무조건 non-referential(하위호환).
    # 길이 제한: 카드 표시 상한(5)에 여유를 둔 10건으로 캡한다.
    prev_entities: list[PrevEntity] = Field(default_factory=list, max_length=10)
    # 직전 턴 분류 intent. 미전송 시 None. (carryover 슬롯, EXPLAIN 소비는 [C] 이후.)
    prev_intent: IntentType | None = Field(default=None)
    # 직전 턴 판단 근거(user_rationale). 미전송 시 None. ([C] 이후 EXPLAIN 소비.)
    prev_reasoning: str | None = Field(default=None, max_length=500)

    @field_validator("prev_intent", mode="before")
    @classmethod
    def _coerce_unknown_intent(cls, v: Any) -> Any:
        """알 수 없는 prev_intent 문자열은 None 으로 폴백한다(내결함성).

        정책: prev_intent 는 현재 carryover 슬롯으로만 보관되고 소비는 [C] 이후이므로
        엄격 검증으로 요청 전체를 422 실패시키는 것은 과하다. Spring 이 미래의 신규/
        오타 intent 값을 회신해도 요청을 깨뜨리지 않도록 unknown → None 으로 받는다.
        (이 정책은 spring-backend 에도 공유됨.)
        """
        if v is None or isinstance(v, IntentType):
            return v
        if isinstance(v, str) and v not in IntentType.__members__:
            return None
        return v


class ChatResponse(BaseModel):
    message_id: int
    answer: str  # 에이전트가 생성한 자연어 답변 (AgentState.answer)
    intent: IntentType | None = None  # 분류된 사용자 의도
    title: str | None = None  # 대화 제목. title_needed=True인 첫 메시지에서만 채워진다.
