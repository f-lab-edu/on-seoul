package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;

import java.util.Set;

/**
 * 1차 발송 채널(Knock) 장애 시 호출되는 fallback 아웃바운드 포트.
 *
 * <h3>Fallback 트리거 시점</h3>
 * <p>{@link dev.jazzybyte.onseoul.notification.adapter.out.knock.ResilientPushNotificationAdapter}가
 * Knock 호출에서 {@link RuntimeException}을 잡으면 이 포트를 호출한다.
 * Scheduler/TxHelper는 fallback 여부를 알지 못한다 — {@link PushNotificationPort} 계약이 동일하다.</p>
 *
 * <h3>구현체 후보</h3>
 * <ul>
 *   <li>{@code SmtpFallbackNotificationAdapter} — JavaMailSender 직접 SMTP 발송 (EMAIL 채널)</li>
 *   <li>{@code LogOnlyFallbackNotificationAdapter} — 로그·메트릭만 기록 (현재 기본값, 스텁)</li>
 * </ul>
 *
 * <h3>멱등성</h3>
 * <p>구현체는 {@code dispatchId}를 idempotency key로 사용해야 한다.</p>
 */
public interface FallbackNotificationPort {

    /**
     * Knock 장애 시 대체 수단으로 알림을 발송한다.
     *
     * @param recipient  수신자 연락처 (userId + email + phoneNumber)
     * @param content    발송 콘텐츠 (title + summary + 구조화 서비스 카드)
     * @param dispatchId idempotency key
     * @param channels   원래 발송 대상이었던 채널 목록
     * @param reason     fallback 트리거 원인
     * @param cause      1차 발송에서 발생한 원본 예외 (null 가능)
     */
    void sendFallback(
            UserContact recipient,
            NotificationContent content,
            Long dispatchId,
            Set<NotificationChannel> channels,
            FallbackReason reason,
            Throwable cause
    );
}
