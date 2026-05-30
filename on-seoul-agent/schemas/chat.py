"""on-seoul-api(Spring Boot)와의 채팅 API 계약 스키마.

네이밍 근거
- 엔드포인트가 /chat/stream이고 호출 경로가 프론트엔드 → on-seoul-api → on-seoul-agent 이므로
  API 계약은 채팅 맥락을 따른다.
- request.message : 사용자가 채팅창에 입력한 텍스트. chat_messages.content(role=user)에 저장된다.
- response.answer : 에이전트가 생성한 자연어 답변.  chat_messages.content(role=assistant)에 저장된다.
  내부 AgentState.answer와 동일한 개념이며, 요청 필드 message와의 혼동을 피하기 위해 다른 이름을 사용한다.
"""

from pydantic import BaseModel, Field

from schemas.state import IntentType


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


class ChatResponse(BaseModel):
    message_id: int
    answer: str  # 에이전트가 생성한 자연어 답변 (AgentState.answer)
    intent: IntentType | None = None  # 분류된 사용자 의도
    title: str | None = None  # 대화 제목. title_needed=True인 첫 메시지에서만 채워진다.
