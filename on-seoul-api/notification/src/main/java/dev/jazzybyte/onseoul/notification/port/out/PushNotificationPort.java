package dev.jazzybyte.onseoul.notification.port.out;

/**
 * 알림 발송 아웃바운드 포트.
 * 구현체는 SMS 또는 이메일 발송을 담당한다.
 *
 * @throws RuntimeException 발송 실패 시. 호출자는 markFailed()를 호출해야 한다.
 */
public interface PushNotificationPort {
    /**
     * 사용자에게 알림을 발송한다.
     *
     * @param userId     수신자 userId
     * @param title      알림 제목
     * @param body       알림 본문
     * @param dispatchId idempotency key
     */
    void send(Long userId, String title, String body, Long dispatchId);
}
