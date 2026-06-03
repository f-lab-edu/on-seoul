import { ChatConversation } from "@/components/chat/chat-conversation";

/**
 * 챗봇 메인 (새 대화 시작).
 * 첫 질의로 방이 생성되면 ChatConversation이 init.room_id로 URL을 /chat/{id}로 교체한다.
 * 인증 가드/공통 헤더는 상위 (chat)/layout.tsx에 위임.
 */
export default function ChatPage() {
  return <ChatConversation welcome />;
}
