package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;

import java.util.Set;

/**
 * 알림 발송 아웃바운드 포트.
 * 구현체는 지정된 채널로 발송을 담당한다.
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
     * @param channels   발송할 채널 목록 (EMAIL, SMS)
     */
    void send(Long userId, String title, String body, Long dispatchId, Set<NotificationChannel> channels);
}
