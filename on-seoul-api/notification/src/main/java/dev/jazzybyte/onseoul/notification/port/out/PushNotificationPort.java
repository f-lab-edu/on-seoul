package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;

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
     * @param recipient  수신자 연락처 (userId + email + phoneNumber). 발송 제공자에 인라인 등록됨
     * @param content    발송 콘텐츠 (title + summary + 구조화 서비스 카드)
     * @param dispatchId idempotency key
     * @param channels   발송할 채널 목록 (EMAIL, SMS)
     */
    void send(UserContact recipient, NotificationContent content, Long dispatchId, Set<NotificationChannel> channels);
}
