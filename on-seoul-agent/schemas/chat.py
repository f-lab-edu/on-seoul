"""on-seoul-api(Spring Boot)와의 채팅 API 계약 스키마.

네이밍 근거
- 엔드포인트가 /chat/stream이고 호출 경로가 프론트엔드 → on-seoul-api → on-seoul-agent 이므로
  API 계약은 채팅 맥락을 따른다.
- request.message : 사용자가 채팅창에 입력한 텍스트. chat_messages.content(role=user)에 저장된다.
- response.answer : 에이전트가 생성한 자연어 답변.  chat_messages.content(role=assistant)에 저장된다.
  내부 AgentState.answer와 동일한 개념이며, 요청 필드 message와의 혼동을 피하기 위해 다른 이름을 사용한다.
"""

from pydantic import BaseModel

from schemas.state import IntentType


class ChatRequest(BaseModel):
    room_id: int
    message_id: int
    message: str  # 사용자 채팅 입력. on-seoul-api가 릴레이한다.


class ChatResponse(BaseModel):
    message_id: int
    answer: str  # 에이전트가 생성한 자연어 답변 (AgentState.answer)
    intent: IntentType | None = None  # 분류된 사용자 의도
    title: str | None = None  # 대화 제목. title_needed=True인 첫 메시지에서만 채워진다.
