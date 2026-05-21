package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;

/**
 * ADR-0004 기반 알림 배치 스케줄러.
 *
 * <p>5분 fixedDelay로 실행되며, 가상 스레드 풀 + Semaphore(4)로 동시 처리를 제한한다.
 * 트랜잭션 경계는 {@link NotificationTxHelper}에 위임한다.
 */
@Slf4j
@Component
public class NotificationScheduler {

    static final int MAX_ATTEMPTS = 5;
    private static final int CONCURRENCY = 4;

    private final LoadSubscriptionPort loadSubscriptionPort;
    private final LoadDispatchPort loadDispatchPort;
    private final LoadUserContactPort loadUserContactPort;
    private final TemplateGenerationPort templateGenerationPort;
    private final PushNotificationPort pushNotificationPort;
    private final NotificationTxHelper txHelper;
    private final MeterRegistry meterRegistry;

    private final ExecutorService executor =
            Executors.newVirtualThreadPerTaskExecutor();
    private final Semaphore semaphore = new Semaphore(CONCURRENCY);

    public NotificationScheduler(
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadDispatchPort loadDispatchPort,
            final LoadUserContactPort loadUserContactPort,
            final TemplateGenerationPort templateGenerationPort,
            final PushNotificationPort pushNotificationPort,
            final NotificationTxHelper txHelper,
            final MeterRegistry meterRegistry) {
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadDispatchPort = loadDispatchPort;
        this.loadUserContactPort = loadUserContactPort;
        this.templateGenerationPort = templateGenerationPort;
        this.pushNotificationPort = pushNotificationPort;
        this.txHelper = txHelper;
        this.meterRegistry = meterRegistry;
    }

    @Scheduled(fixedDelayString = "${notification.scheduler.fixed-delay-ms:300000}")
    public void processAllSubscriptions() {
        log.debug("[NotificationScheduler] 스케줄 실행 시작");
        List<NotificationSubscription> subscriptions = loadSubscriptionPort.loadAll();

        for (NotificationSubscription sub : subscriptions) {
            executor.submit(() -> processSubscription(sub));
        }
    }

    private void processSubscription(NotificationSubscription sub) {
        try {
            semaphore.acquire();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return;
        }
        try {
            List<ServiceChange> newChanges;
            try {
                newChanges = txHelper.txA(sub, MAX_ATTEMPTS);
            } catch (Exception e) {
                log.warn("[NotificationScheduler] TX A 실패: subscriptionId={}, error={}",
                        sub.getId(), e.getMessage());
                return;
            }

            if (newChanges.isEmpty()) {
                return;
            }

            for (ServiceChange change : newChanges) {
                processChange(sub, change);
            }
        } finally {
            semaphore.release();
        }
    }

    private void processChange(NotificationSubscription sub, ServiceChange change) {
        Optional<NotificationDispatch> dispatchOpt =
                loadDispatchPort.loadRetryable(sub.getId(), change.id(), MAX_ATTEMPTS);

        if (dispatchOpt.isEmpty()) {
            log.debug("[NotificationScheduler] retryable dispatch 없음: subscriptionId={}, changeId={}",
                    sub.getId(), change.id());
            return;
        }

        NotificationDispatch dispatch = dispatchOpt.get();

        // TX 밖: 템플릿 생성 (fallback 포함)
        TemplateResult template = templateGenerationPort.generate(
                new NotificationTemplateRequest(
                        change.serviceId(),
                        change.changeType(),
                        change.fieldName(),
                        change.oldValue(),
                        change.newValue()
                ));

        Counter.builder("notification.template.source")
                .tag("source", template.source().name())
                .register(meterRegistry)
                .increment();

        // TX 밖: 연락처 조회 (미등록 시 userId만으로 fallback)
        UserContact recipient = loadUserContactPort.loadContact(sub.getUserId())
                .orElseGet(() -> {
                    log.warn("[NotificationScheduler] 연락처 미등록 — userId만으로 발송 시도: userId={}",
                            sub.getUserId());
                    return new UserContact(sub.getUserId(), null, null);
                });

        // TX 밖: 푸시 발송
        try {
            pushNotificationPort.send(
                    recipient,
                    template.title(),
                    template.body(),
                    dispatch.getId(),
                    sub.getChannels());

            try {
                txHelper.txBSuccess(dispatch, sub, change,
                        template.title(), template.body(), template.source());
            } catch (Exception e) {
                log.error("[NotificationScheduler] TX B(성공) 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }

            Counter.builder("notification.dispatch.attempts")
                    .tag("result", "success")
                    .register(meterRegistry)
                    .increment();

        } catch (RuntimeException pushEx) {
            try {
                txHelper.txBFailure(dispatch, pushEx.getMessage(), MAX_ATTEMPTS);

                // DEAD 전환 여부 확인 (markFailed 후 상태 반영)
                if (dispatch.getStatus() == DispatchStatus.DEAD) {
                    Counter.builder("notification.dispatch.dead")
                            .register(meterRegistry)
                            .increment();
                }
            } catch (Exception e) {
                log.error("[NotificationScheduler] TX B(실패) 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }

            Counter.builder("notification.dispatch.attempts")
                    .tag("result", "failed")
                    .register(meterRegistry)
                    .increment();
        }
    }
}
