import { ApiError } from "@/lib/api-client";

/**
 * 알림 도메인 ApiError → 한국어 메시지.
 * 명세 §7 에러 코드 매핑.
 */
export function notificationErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "잠시 후 다시 시도해 주세요.";
  switch (err.status) {
    case 400:
      return "입력값이 올바르지 않습니다.";
    case 403:
      return "권한이 없습니다.";
    case 404:
      return "구독을 찾을 수 없습니다.";
    case 409:
      return "이미 구독 중인 서비스입니다.";
    case 500:
      return "잠시 후 다시 시도해 주세요.";
    default:
      return "잠시 후 다시 시도해 주세요.";
  }
}
