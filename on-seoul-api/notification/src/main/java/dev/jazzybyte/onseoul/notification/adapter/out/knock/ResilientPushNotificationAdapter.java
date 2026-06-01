package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * Knock 장애 대응 탄력성 데코레이터.
 *
 * <p>{@link PushNotificationPort}의 {@link Primary} 구현체.
 * 두 가지 탄력성 레이어를 제공한다:
 * <ol>
 *   <li><b>CircuitBreaker</b> — Knock 연속 실패 시 서킷을 열어 fast-fail. 불필요한 타임아웃 대기를
 *       방지하고 배치 실행 시간을 단축한다. 설정: {@code application.yml resilience4j.circuitbreaker.instances.knock}</li>
 *   <li><b>Fallback 라우팅</b> — Knock 실패(또는 서킷 오픈) 시 {@link FallbackNotificationPort}로
 *       대체 발송을 시도한다. 현재 {@link LogOnlyFallbackNotificationAdapter}가 기본값(로그·메트릭만 기록)이며,
 *       추후 OneSignal 구현체로 교체 예정이다.</li>
 * </ol>
 *
 * <h3>예외 전파 정책</h3>
 * <p>Knock 실패 또는 서킷 오픈 시 fallback 호출 후 <b>원본 예외를 rethrow</b>한다.
 * 스케줄러({@code NotificationScheduler})가 예외를 받아 {@code txBFailure()}를 호출해
 * dispatch 를 {@code FAILED}로 기록하고, {@code DispatchRetryScheduler}가 Knock 복구 후 재시도한다.
 *
 * <p><b>주의:</b> OneSignal fallback 구현 시, OneSignal 발송 성공 여부에 따라 rethrow 여부를
 * 결정하도록 {@link FallbackNotificationPort} 계약을 boolean 반환값으로 확장하거나
 * 별도 예외 타입으로 성공/실패를 구분해야 한다(중복 발송 방지).
 *
 * <h3>TODO</h3>
 * <ul>
 *   <li>{@code OneSignalFallbackNotificationAdapter} 구현 후 {@link LogOnlyFallbackNotificationAdapter} 교체</li>
 *   <li>OneSignal 발송 성공 시 rethrow 생략하도록 설계 재검토 (중복 발송 방지)</li>
 * </ul>
 *
 * @see KnockNotificationAdapter
 * @see FallbackNotificationPort
 * @see LogOnlyFallbackNotificationAdapter
 */
@Slf4j
@Primary
@Component
public class ResilientPushNotificationAdapter implements PushNotificationPort {

    static final String CIRCUIT_BREAKER_NAME = "knock";
    private static final String METRIC_FALLBACK = "notification.push.fallback";

    private final PushNotificationPort primary;
    private final FallbackNotificationPort fallback;
    private final CircuitBreaker circuitBreaker;
    /** FallbackReason별 counter를 생성자에서 미리 등록하여 매 호출마다 lookup overhead를 제거한다. */
    private final Map<FallbackReason, Counter> fallbackCounters;

    public ResilientPushNotificationAdapter(
            @Qualifier("knockPrimary") PushNotificationPort primary,
            FallbackNotificationPort fallback,
            CircuitBreakerRegistry circuitBreakerRegistry,
            MeterRegistry meterRegistry) {
        this.primary = primary;
        this.fallback = fallback;
        this.circuitBreaker = circuitBreakerRegistry.circuitBreaker(CIRCUIT_BREAKER_NAME);
        this.fallbackCounters = Arrays.stream(FallbackReason.values())
                .collect(Collectors.toUnmodifiableMap(
                        r -> r,
                        r -> Counter.builder(METRIC_FALLBACK)
                                .tag("reason", r.name())
                                .register(meterRegistry)
                ));
    }

    /**
     * Knock으로 알림을 발송한다.
     *
     * <p>Knock 실패 또는 서킷 오픈 시 fallback을 호출한 뒤 예외를 rethrow한다.
     * 호출자({@code NotificationScheduler})가 예외를 받아 dispatch를 {@code FAILED}로 기록한다.
     *
     * @throws CallNotPermittedException 서킷 오픈 상태 (fast-fail)
     * @throws RuntimeException          Knock 발송 실패
     */
    @Override
    public void send(UserContact recipient, NotificationContent content,
                     Long dispatchId, Set<NotificationChannel> channels) {
        Runnable decorated = CircuitBreaker.decorateRunnable(circuitBreaker,
                () -> primary.send(recipient, content, dispatchId, channels));
        try {
            decorated.run();
        } catch (CallNotPermittedException e) {
            // 서킷 오픈: Knock 호출 없이 fast-fail
            handleFallback(FallbackReason.KNOCK_CIRCUIT_OPEN, e, recipient, content,
                    dispatchId, channels);
            throw e;
        } catch (RuntimeException e) {
            // Knock 발송 실패: 회로 차단기가 실패 기록 후 예외를 그대로 전파
            handleFallback(classifyReason(e), e, recipient, content, dispatchId, channels);
            throw e;
        }
    }

    private void handleFallback(FallbackReason reason, Throwable cause,
                                UserContact recipient, NotificationContent content,
                                Long dispatchId, Set<NotificationChannel> channels) {
        log.warn("[ResilientPush] Knock 발송 실패 — fallback 실행: dispatchId={}, reason={}, exceptionType={}",
                dispatchId, reason, cause.getClass().getSimpleName());
        fallbackCounters.get(reason).increment();
        fallback.sendFallback(recipient, content, dispatchId, channels, reason, cause);
    }

    /**
     * {@link KnockDispatchException}이 보유한 구조화된 {@link FallbackReason}을 반환한다.
     *
     * <p>Knock 계층이 항상 {@link KnockDispatchException}을 던지므로
     * 문자열 매칭 없이 정확한 분류가 가능하다.
     * 예상치 못한 예외 타입은 {@link FallbackReason#KNOCK_UNAVAILABLE}로 처리한다.
     */
    private FallbackReason classifyReason(RuntimeException e) {
        if (e instanceof KnockDispatchException kde) {
            return kde.getReason();
        }
        return FallbackReason.KNOCK_UNAVAILABLE;
    }
}
