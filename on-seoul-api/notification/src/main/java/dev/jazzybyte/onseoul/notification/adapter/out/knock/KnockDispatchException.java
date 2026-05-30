package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;

/**
 * Knock 워크플로우 트리거 실패 시 발생하는 예외.
 *
 * <p>{@link FallbackReason}을 구조화된 필드로 보유하여
 * {@link ResilientPushNotificationAdapter}가 문자열 매칭 없이 정확하게 분류할 수 있다.</p>
 */
class KnockDispatchException extends RuntimeException {

    private final FallbackReason reason;

    KnockDispatchException(FallbackReason reason, String message, Throwable cause) {
        super(message, cause);
        this.reason = reason;
    }

    FallbackReason getReason() {
        return reason;
    }
}
