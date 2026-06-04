/**
 * 대화이력 관리 타입.
 * 정본: on-seoul-api/chat/adapter/in/web/ChatHistoryController.java
 *   (RoomSummaryResponse, RoomListResponse, ChatHistoryResponse, MessageResponse)
 * 가이드: on-seoul-front/docs/2026-06-02-frontend-chat-history.md §7
 *
 * 기존 types/chat.ts(ChatRoom/ChatMessage, `id` 기반)와 필드 규약이 다르므로
 * (여기는 `roomId`/`seq`/`titleGenerated`) 별도 파일로 분리한다.
 */

export type ChatRole = "USER" | "ASSISTANT";

/** GET /api/chat/rooms 응답의 대화방 요약 항목. */
export interface RoomSummary {
  roomId: number;
  title: string;
  /** 제목이 AI로 자동 생성되었는지. */
  titleGenerated: boolean;
  createdAt: string; // ISO 8601 (UTC)
  updatedAt: string; // ISO 8601 (UTC)
}

/** GET /api/chat/rooms 응답. `nextCursor`가 null이면 마지막 페이지. */
export interface RoomListResponse {
  rooms: RoomSummary[];
  nextCursor: string | null;
}

/** GET /api/chat/rooms/{roomId}/messages 응답의 메시지 항목. */
export interface ChatMessageItem {
  seq: number;
  role: ChatRole;
  content: string;
  createdAt: string; // ISO 8601 (UTC)
}

/** GET /api/chat/rooms/{roomId}/messages 응답. 메시지는 seq 오름차순 전체 반환. */
export interface ChatHistoryResponse {
  roomId: number;
  title: string;
  messages: ChatMessageItem[];
}
