package dev.jazzybyte.onseoul.exception;

import lombok.Getter;
import lombok.RequiredArgsConstructor;

/**
 * 애플리케이션 전역 에러 코드.
 *
 * <p>각 코드는 HTTP 상태 코드 및 사람이 읽을 수 있는 메시지를 보유한다.
 * 에러 응답 직렬화 시 {@code code} 값을 클라이언트에 노출한다.</p>
 */
@Getter
@RequiredArgsConstructor
public enum ErrorCode {

    // ── 인증 (AUTH_*) ──────────────────────────────────────────────────────
    UNAUTHORIZED(401, "UNAUTHORIZED", "인증이 필요합니다."),
    FORBIDDEN(403, "FORBIDDEN", "접근 권한이 없습니다."),
    INVALID_TOKEN(401, "INVALID_TOKEN", "유효하지 않은 토큰입니다."),
    EXPIRED_TOKEN(401, "EXPIRED_TOKEN", "만료된 토큰입니다."),
    INVALID_REFRESH_TOKEN(401, "INVALID_REFRESH_TOKEN", "유효하지 않은 리프레시 토큰입니다."),

    // ── 수집 파이프라인 (COLLECT_*) ────────────────────────────────────────
    COLLECT_API_SERVER_ERROR(502, "COLLECT_API_SERVER_ERROR", "외부 API 서버 오류가 발생했습니다."),
    COLLECT_API_CLIENT_ERROR(400, "COLLECT_API_CLIENT_ERROR", "외부 API 요청이 잘못되었습니다."),
    COLLECT_API_PARSE_ERROR(500, "COLLECT_API_PARSE_ERROR", "외부 API 응답 파싱에 실패했습니다."),
    COLLECT_API_TIMEOUT(504, "COLLECT_API_TIMEOUT", "외부 API 응답 시간이 초과되었습니다."),

    // ── AI 서비스 (AI_*) ───────────────────────────────────────────────────
    AI_SERVICE_ERROR(502, "AI_SERVICE_ERROR", "AI 서비스 오류가 발생했습니다."),

    // ── 채팅 (CHAT_*) ──────────────────────────────────────────────────────
    CHAT_ROOM_NOT_FOUND(404, "CHAT_ROOM_NOT_FOUND", "대화방을 찾을 수 없습니다."),

    // ── 공통 서버 오류 (SERVER_*) ──────────────────────────────────────────
    SERVER_ERROR(500, "SERVER_ERROR", "서버 내부 오류가 발생했습니다."),
    INVALID_INPUT(400, "INVALID_INPUT", "입력값이 올바르지 않습니다.");

    /** HTTP 응답 상태 코드 */
    private final int httpStatus;

    /** 클라이언트에 노출되는 에러 식별자 */
    private final String code;

    /** 기본 사용자 메시지 */
    private final String defaultMessage;
}
