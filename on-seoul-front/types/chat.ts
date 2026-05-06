/**
 * 채팅 관련 타입.
 * 정본: on-seoul-api — query_history (ChatRoom, ChatMessage)
 */

/** GET /chat/rooms 응답 항목 */
export interface ChatRoom {
  id: number;
  title: string | null; // 첫 응답 완료 전까지 null
  createdAt: string;    // ISO 8601
  updatedAt: string;
}

export type MessageRole = "USER" | "ASSISTANT";

/** GET /chat/rooms/{id}/messages 응답 항목 */
export interface ChatMessage {
  id: number;
  roomId: number;
  role: MessageRole;
  content: string;
  createdAt: string;    // ISO 8601
}
