/**
 * 인증 관련 타입.
 * 정본: on-seoul-api — users 테이블 / /auth/me 응답
 */

export type UserStatus = "ACTIVE" | "SUSPENDED" | "DELETED";

/** GET /auth/me 응답 */
export interface User {
  id: number;
  nickname: string;
  status: UserStatus;
}

/** /oauth/callback 쿼리 파라미터 */
export type OAuthCallbackStatus = "success";
export type OAuthCallbackError = "forbidden";
