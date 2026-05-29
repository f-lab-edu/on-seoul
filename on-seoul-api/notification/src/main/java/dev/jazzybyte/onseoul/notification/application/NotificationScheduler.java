package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * ADR-0004 per-batch 알림 스케줄러.
 *
 * <p>흐름:
 * <ol>
 *   <li>NotificationBatch INSERT (status=RUNNING)</li>
 *   <li>구독별 처리 (가상 스레드 풀 + Semaphore(4))
 *     <ul>
 *       <li>TX A — 변경 조회 + dispatch saveIfAbsent</li>
 *       <li>TX 밖 — 템플릿 생성 + 푸시 발송</li>
 *       <li>TX B — 결과 갱신 (성공 시 last_notified_at = batch.startedAt 전진)</li>
 *     </ul>
 *   </li>
 *   <li>모든 구독 완료 대기 → NotificationBatch UPDATE (status, finished_at, sent_count, failed_count)</li>
 * </ol>
 */
@Slf4j
@Component
public class NotificationScheduler {

    private static final int CONCURRENCY = 4;

    private final LoadSubscriptionPort loadSubscriptionPort;
    private final LoadUserContactPort loadUserContactPort;
    private final TemplateGenerationPort templateGenerationPort;
    private final PushNotificationPort pushNotificationPort;
    private final SaveBatchPort saveBatchPort;
    private final NotificationTxHelper txHelper;
    private final MeterRegistry meterRegistry;

    public NotificationScheduler(
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadUserContactPort loadUserContactPort,
            final TemplateGenerationPort templateGenerationPort,
            final PushNotificationPort pushNotificationPort,
            final SaveBatchPort saveBatchPort,
            final NotificationTxHelper txHelper,
            final MeterRegistry meterRegistry) {
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadUserContactPort = loadUserContactPort;
        this.templateGenerationPort = templateGenerationPort;
        this.pushNotificationPort = pushNotificationPort;
        this.saveBatchPort = saveBatchPort;
        this.txHelper = txHelper;
        this.meterRegistry = meterRegistry;
    }

    @Scheduled(fixedDelayString = "${notification.scheduler.fixed-delay-ms:300000}")
    public void processAllSubscriptions() {
        log.debug("[NotificationScheduler] 스케줄 실행 시작");

        // 1. 배치 INSERT (RUNNING). 이 실패는 배치 전체 중단 — Batch row 자체가 없으므로 다음 tick에서 재시작.
        NotificationBatch batch;
        try {
            batch = saveBatchPort.insertRunning(NotificationBatch.start());
        } catch (Exception e) {
            log.error("[NotificationScheduler] Batch INSERT 실패 — 이번 tick 중단: {}", e.getMessage(), e);
            return;
        }

        List<NotificationSubscription> subscriptions;
        try {
            subscriptions = loadSubscriptionPort.loadAll();
        } catch (Exception e) {
            log.error("[NotificationScheduler] 구독 조회 실패 — 배치 FAILED 처리: batchId={}, error={}",
                    batch.getId(), e.getMessage(), e);
            batch.fail(0, 0);
            safeUpdateBatch(batch);
            return;
        }

        AtomicInteger sentCount = new AtomicInteger(0);
        AtomicInteger failedCount = new AtomicInteger(0);
        Semaphore semaphore = new Semaphore(CONCURRENCY);

        // 가상 스레드 ExecutorService는 try-with-resources로 닫으면 모든 작업 완료를 대기한다 (JEP 453 close()).
        try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
            for (NotificationSubscription sub : subscriptions) {
                executor.submit(() -> processSubscription(batch, sub, semaphore, sentCount, failedCount));
            }
            // try-with-resources 종료 시 ExecutorService.close()가 awaitTermination 한다.
        } catch (Exception e) {
            log.error("[NotificationScheduler] 배치 실행 중 예외 — FAILED 처리: batchId={}, error={}",
                    batch.getId(), e.getMessage(), e);
            batch.fail(sentCount.get(), failedCount.get());
            safeUpdateBatch(batch);
            return;
        }

        batch.complete(sentCount.get(), failedCount.get());
        safeUpdateBatch(batch);
        log.info("[NotificationScheduler] 배치 완료: batchId={}, sent={}, failed={}",
                batch.getId(), sentCount.get(), failedCount.get());
    }

    private void processSubscription(NotificationBatch batch, NotificationSubscription sub,
                                     Semaphore semaphore,
                                     AtomicInteger sentCount, AtomicInteger failedCount) {
        try {
            semaphore.acquire();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return;
        }
        try {
            NotificationTxHelper.TxAResult txA;
            try {
                txA = txHelper.txA(batch, sub);
            } catch (Exception e) {
                log.warn("[NotificationScheduler] TX A 실패: subscriptionId={}, error={}",
                        sub.getId(), e.getMessage());
                failedCount.incrementAndGet();
                return;
            }

            if (txA.changes().isEmpty() || txA.dispatch().isEmpty()) {
                // 변경 없음 또는 (batch_id, subscription_id) 중복 — 발송 skip
                return;
            }

            dispatchOne(batch, sub, txA.changes(), txA.dispatch().get(), sentCount, failedCount);
        } finally {
            semaphore.release();
        }
    }

    private void dispatchOne(NotificationBatch batch, NotificationSubscription sub,
                             List<ServiceChange> changes, NotificationDispatch dispatch,
                             AtomicInteger sentCount, AtomicInteger failedCount) {
        // TX 밖: 배치 템플릿 생성 (구독 1건 = AI 호출 1회)
        TemplateResult template = templateGenerationPort.generate(
                new NotificationTemplateRequest(sub.getServiceId(), toChangeItems(changes)));

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
                txHelper.txBSuccess(dispatch, sub, batch,
                        template.title(), template.body(), template.source());
            } catch (Exception e) {
                log.error("[NotificationScheduler] TX B(성공) 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }

            sentCount.incrementAndGet();
            Counter.builder("notification.dispatch.attempts")
                    .tag("result", "success")
                    .register(meterRegistry)
                    .increment();

        } catch (RuntimeException pushEx) {
            try {
                txHelper.txBFailure(dispatch,
                        template.title(), template.body(), template.source(),
                        pushEx.getMessage());
            } catch (Exception e) {
                log.error("[NotificationScheduler] TX B(실패) 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }

            failedCount.incrementAndGet();
            Counter.builder("notification.dispatch.attempts")
                    .tag("result", "failed")
                    .register(meterRegistry)
                    .increment();
            Counter.builder("notification.dispatch.failed.total")
                    .register(meterRegistry)
                    .increment();
        }
    }

    private List<NotificationTemplateRequest.ChangeItem> toChangeItems(List<ServiceChange> changes) {
        return changes.stream()
                .map(c -> new NotificationTemplateRequest.ChangeItem(
                        c.changeType(), c.fieldName(), c.oldValue(), c.newValue()))
                .toList();
    }

    private void safeUpdateBatch(NotificationBatch batch) {
        try {
            saveBatchPort.update(batch);
        } catch (Exception e) {
            log.error("[NotificationScheduler] Batch UPDATE 실패: batchId={}, error={}",
                    batch.getId(), e.getMessage(), e);
        }
    }
}
