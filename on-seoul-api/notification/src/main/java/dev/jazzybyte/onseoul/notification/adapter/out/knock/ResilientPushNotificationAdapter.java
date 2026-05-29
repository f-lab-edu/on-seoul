package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

import java.util.Set;

/**
 * Knock 장애 대응 탄력성 데코레이터.
 *
 * <p>{@link PushNotificationPort}의 {@link Primary} 구현체.
 * {@link KnockNotificationAdapter}를 1차로 호출하고, 실패 시
 * {@link FallbackNotificationPort}로 라우팅한다.
 * {@code NotificationScheduler} / {@code NotificationTxHelper}는 변경 없이 동작한다.</p>
 *
 * <h3>TODO — Phase 6-2 잔여 구현 항목</h3>
 * <ol>
 *   <li>Resilience4j {@code CircuitBreaker} 적용 — Knock 연속 실패 시 fast-fail +
 *       {@link FallbackReason#KNOCK_CIRCUIT_OPEN} 트리거</li>
 *   <li>{@link FallbackNotificationPort} 실 구현체 선택 — SMTP / in-app 중 확정 후
 *       {@code LogOnlyFallbackNotificationAdapter} 교체</li>
 * </ol>
 *
 * @see KnockNotificationAdapter
 * @see FallbackNotificationPort
 * @see LogOnlyFallbackNotificationAdapter
 */
@Slf4j
@Primary
@Component
public class ResilientPushNotificationAdapter implements PushNotificationPort {

    private static final String METRIC_FALLBACK = "notification.push.fallback";

    private final PushNotificationPort primary;
    private final FallbackNotificationPort fallback;
    // FallbackReason별 counter를 생성자에서 미리 등록하여 매 호출마다 lookup overhead를 제거한다.
    private final java.util.Map<FallbackReason, io.micrometer.core.instrument.Counter> fallbackCounters;

    public ResilientPushNotificationAdapter(
            @Qualifier("knockPrimary") PushNotificationPort primary,
            FallbackNotificationPort fallback,
            MeterRegistry meterRegistry) {
        this.primary = primary;
        this.fallback = fallback;
        this.fallbackCounters = java.util.Arrays.stream(FallbackReason.values())
                .collect(java.util.stream.Collectors.toUnmodifiableMap(
                        r -> r,
                        r -> io.micrometer.core.instrument.Counter.builder(METRIC_FALLBACK)
                                .tag("reason", r.name())
                                .register(meterRegistry)
                ));
    }

    @Override
    public void send(UserContact recipient, String title, String body,
                     Long dispatchId, Set<NotificationChannel> channels) {
        try {
            primary.send(recipient, title, body, dispatchId, channels);
        } catch (RuntimeException e) {
            FallbackReason reason = classifyReason(e);
            log.warn("[ResilientPush] Knock 발송 실패 — fallback 실행: dispatchId={}, reason={}, exceptionType={}",
                    dispatchId, reason, e.getClass().getSimpleName());

            fallbackCounters.get(reason).increment();

            fallback.sendFallback(recipient, title, body, dispatchId, channels, reason, e);
        }
    }

    /**
     * {@link KnockDispatchException}이 보유한 구조화된 {@link FallbackReason}을 반환한다.
     *
     * <p>Knock 계층이 항상 {@link KnockDispatchException}을 던지므로
     * 문자열 매칭 없이 정확한 분류가 가능하다.
     * 예상치 못한 예외 타입이 올 경우 {@link FallbackReason#KNOCK_UNAVAILABLE}로 처리한다.</p>
     */
    private FallbackReason classifyReason(RuntimeException e) {
        if (e instanceof KnockDispatchException kde) {
            return kde.getReason();
        }
        return FallbackReason.KNOCK_UNAVAILABLE;
    }
}
