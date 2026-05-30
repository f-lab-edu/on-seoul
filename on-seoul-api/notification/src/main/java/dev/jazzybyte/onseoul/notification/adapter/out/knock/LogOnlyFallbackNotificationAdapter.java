package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.stereotype.Component;

import java.util.Set;

/**
 * Fallback 기본 구현체 — 로그·메트릭만 기록, 실제 발송 없음.
 *
 * <p>{@code @ConditionalOnMissingBean(FallbackNotificationPort.class)}로 선언되어
 * 실 구현체(SMTP, in-app 등)가 빈으로 등록되면 자동으로 교체된다.</p>
 *
 * <h3>Phase 6-2 교체 대상</h3>
 * <ul>
 *   <li>{@code SmtpFallbackNotificationAdapter} — EMAIL 채널에 JavaMailSender 직접 발송</li>
 *   <li>{@code InAppFallbackNotificationAdapter} — {@code notification_outbox} 테이블에 저장</li>
 * </ul>
 *
 * @see FallbackNotificationPort
 */
@Slf4j
@Component
@ConditionalOnMissingBean(value = FallbackNotificationPort.class, ignored = LogOnlyFallbackNotificationAdapter.class)
public class LogOnlyFallbackNotificationAdapter implements FallbackNotificationPort {

    @Override
    public void sendFallback(UserContact recipient, String title, String body,
                             Long dispatchId, Set<NotificationChannel> channels,
                             FallbackReason reason, Throwable cause) {
        log.error(
                "[Fallback] Knock 장애 — 실 fallback 미구현. 알림 유실 가능성 있음. " +
                "dispatchId={}, userId={}, channels={}, reason={}",
                dispatchId, recipient.userId(), channels, reason, cause
        );
        // TODO Phase 6-2: SMTP 또는 in-app fallback 구현체로 교체
    }
}
