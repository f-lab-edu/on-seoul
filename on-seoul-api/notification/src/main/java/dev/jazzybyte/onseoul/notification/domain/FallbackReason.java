package dev.jazzybyte.onseoul.notification.domain;

/**
 * 1차 발송 채널(Knock) 장애 시 fallback이 트리거된 원인.
 *
 * <p>{@link dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort}의
 * 구현체는 이 값을 참조하여 로깅·메트릭·발송 방식을 조정할 수 있다.</p>
 */
public enum FallbackReason {

    /** Knock API 서버 연결 불가 (ConnectException, UnknownHostException 등) */
    KNOCK_UNAVAILABLE,

    /** Knock API 응답 타임아웃 */
    KNOCK_TIMEOUT,

    /** Knock 서킷 브레이커 오픈 상태 — 빠른 실패(fast-fail) */
    KNOCK_CIRCUIT_OPEN,

    /** Knock이 5xx 응답 반환 (서버 오류) */
    KNOCK_SERVER_ERROR,

    /** 모든 채널에 대해 연락처가 없어 발송 불가 */
    NO_CONTACT
}
