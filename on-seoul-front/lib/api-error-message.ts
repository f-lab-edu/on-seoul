import { ApiError } from "@/lib/api-client";

/**
 * 알림 도메인 ApiError → 한국어 메시지.
 * 명세 §7 에러 코드 매핑.
 *
 * 2026-05-28: 구독이 조건 기반으로 전환되며 `(user_id, service_id)` 중복 방지
 * 제약이 사라졌다. `409 Conflict`는 더 이상 발생하지 않으므로 별도 분기를 두지
 * 않는다(명세 §7 Note).
 *
 * 400은 빈 조건·키워드 3개 초과·잘못된 키워드 대상이 해당한다. 백엔드가
 * 사용자 친화적인 메시지를 보내면 그대로 노출하고, 없으면 일반 안내로 대체한다.
 */
export function notificationErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "잠시 후 다시 시도해 주세요.";
  switch (err.status) {
    case 400:
      return err.message || "최소 1개 조건을 선택해야 합니다.";
    case 403:
      return "권한이 없습니다.";
    case 404:
      return "구독을 찾을 수 없습니다.";
    case 500:
      return "잠시 후 다시 시도해 주세요.";
    default:
      return "잠시 후 다시 시도해 주세요.";
  }
}

/**
 * 대화이력 도메인 ApiError → 한국어 메시지.
 * 가이드 §6: 본 BC의 에러 바디 키는 `code`/`message`(알림의 `error`와 다름).
 * 404 CHAT_ROOM_NOT_FOUND는 미존재/타인 소유/삭제된 방을 일괄 처리(IDOR 차단).
 * 검증 에러(400)의 `code`는 한글 문자열이라 신뢰하지 않고 HTTP 상태로만 분기한다.
 */
export function chatHistoryErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "잠시 후 다시 시도해 주세요.";
  switch (err.status) {
    case 400:
      return err.message || "요청을 처리할 수 없습니다.";
    case 401:
      return "세션이 만료되었습니다. 다시 로그인해주세요.";
    case 404:
      return "대화방을 찾을 수 없습니다.";
    default:
      return "잠시 후 다시 시도해 주세요.";
  }
}
