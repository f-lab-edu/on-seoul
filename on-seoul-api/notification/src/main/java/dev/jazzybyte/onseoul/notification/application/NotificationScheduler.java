package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;
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

    /**
     * 한 번에 DB에서 읽어 올 구독 청크 크기.
     * keyset 페이지네이션에서 각 청크는 {@code id > lastId ORDER BY id ASC LIMIT CHUNK_SIZE} 쿼리로 조회한다.
     *
     * <p>값을 크게 설정할수록 DB 왕복 횟수가 줄지만 청크당 메모리 사용이 늘어난다.
     * 가상 스레드 풀(Semaphore(4))과의 백프레셔를 고려하면 100 수준이 적합하다.
     */
    static final int SUBSCRIPTION_CHUNK_SIZE = 100;

    private final LoadSubscriptionPort loadSubscriptionPort;
    private final LoadUserContactPort loadUserContactPort;
    private final TemplateGenerationPort templateGenerationPort;
    private final PushNotificationPort pushNotificationPort;
    private final SaveBatchPort saveBatchPort;
    private final LoadBatchPort loadBatchPort;
    private final NotificationTxHelper txHelper;
    private final MeterRegistry meterRegistry;
    private final long staleThresholdMs;

    public NotificationScheduler(
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadUserContactPort loadUserContactPort,
            final TemplateGenerationPort templateGenerationPort,
            final PushNotificationPort pushNotificationPort,
            final SaveBatchPort saveBatchPort,
            final LoadBatchPort loadBatchPort,
            final NotificationTxHelper txHelper,
            final MeterRegistry meterRegistry,
            @Value("${notification.scheduler.stale-threshold-ms:600000}") final long staleThresholdMs) {
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadUserContactPort = loadUserContactPort;
        this.templateGenerationPort = templateGenerationPort;
        this.pushNotificationPort = pushNotificationPort;
        this.saveBatchPort = saveBatchPort;
        this.loadBatchPort = loadBatchPort;
        this.txHelper = txHelper;
        this.meterRegistry = meterRegistry;
        this.staleThresholdMs = staleThresholdMs;
    }

    @Scheduled(fixedDelayString = "${notification.scheduler.fixed-delay-ms:300000}")
    public void processAllSubscriptions() {
        log.debug("[NotificationScheduler] 스케줄 실행 시작");

        // 0. JVM 크래시로 complete()/fail() 호출 없이 종료된 stale RUNNING batch를 회수한다.
        recoverStaleBatches();

        // 1. 배치 INSERT (RUNNING). 이 실패는 배치 전체 중단 — Batch row 자체가 없으므로 다음 tick에서 재시작.
        NotificationBatch batch;
        try {
            batch = saveBatchPort.insertRunning(NotificationBatch.start());
        } catch (Exception e) {
            log.error("[NotificationScheduler] Batch INSERT 실패 — 이번 tick 중단: {}", e.getMessage(), e);
            return;
        }

        AtomicInteger sentCount = new AtomicInteger(0);
        AtomicInteger failedCount = new AtomicInteger(0);
        Semaphore semaphore = new Semaphore(CONCURRENCY);
        boolean chunkLoadFailed = false;

        // 가상 스레드 ExecutorService는 try-with-resources로 닫으면 모든 작업 완료를 대기한다 (JEP 453 close()).
        try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
            // keyset 기반 청크 순회: id > afterId 조건으로 CHUNK_SIZE씩 조회해 submit.
            // 반환 크기 < CHUNK_SIZE이면 마지막 페이지 → 루프 종료.
            // 청크 조회 실패 시 이미 제출된 작업은 완료까지 대기 후 배치 FAILED 처리.
            long afterId = 0L;
            while (true) {
                List<NotificationSubscription> chunk;
                try {
                    chunk = loadSubscriptionPort.loadChunk(afterId, SUBSCRIPTION_CHUNK_SIZE);
                } catch (Exception e) {
                    log.error("[NotificationScheduler] 구독 청크 조회 실패 — 이미 제출된 작업 대기 후 FAILED 처리: "
                                    + "batchId={}, afterId={}, error={}",
                            batch.getId(), afterId, e.getMessage(), e);
                    chunkLoadFailed = true;
                    break;
                }
                for (NotificationSubscription sub : chunk) {
                    executor.submit(() -> processSubscription(batch, sub, semaphore, sentCount, failedCount));
                }
                if (chunk.size() < SUBSCRIPTION_CHUNK_SIZE) {
                    break; // 마지막 페이지
                }
                afterId = chunk.get(chunk.size() - 1).getId();
            }
            // try-with-resources 종료: ExecutorService.close()가 제출된 모든 작업의 완료를 대기.
        } catch (Exception e) {
            log.error("[NotificationScheduler] 배치 실행 중 예외 — FAILED 처리: batchId={}, error={}",
                    batch.getId(), e.getMessage(), e);
            batch.fail(sentCount.get(), failedCount.get());
            safeUpdateBatch(batch);
            return;
        }

        if (chunkLoadFailed) {
            batch.fail(sentCount.get(), failedCount.get());
        } else {
            batch.complete(sentCount.get(), failedCount.get());
        }
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
        // 구독은 더 이상 serviceId에 고정되지 않으므로, 템플릿 요청의 serviceId는
        // 매칭된 변경의 대표 serviceId(첫 변경)에서 가져온다.
        String serviceId = changes.get(0).serviceId();
        TemplateResult template = templateGenerationPort.generate(
                new NotificationTemplateRequest(serviceId, toChangeItems(changes)));

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

    /**
     * JVM 크래시 후 stale RUNNING batch를 FAILED로 회수한다.
     * {@link #staleThresholdMs} 이상 경과한 RUNNING batch 대상.
     *
     * <p>stale-threshold-ms(기본 600,000 ms = 10분)는 fixed-delay-ms(기본 5분) × 2 이상으로
     * 설정하는 것을 권장한다. 임계값 < fixed-delay 로 설정하면 정상 실행 중인 배치를 회수할 위험이 있다.
     *
     * <p>회수된 batch의 sent_count/failed_count는 0으로 기록된다 — 크래시 시점 실제 값 미상.
     */
    private void recoverStaleBatches() {
        try {
            recoverStaleBatches(Instant.now().minusMillis(staleThresholdMs));
        } catch (Exception e) {
            // stale 회수 실패는 이번 tick 배치 INSERT를 막지 않는다.
            // 다음 tick에서 재시도하므로 stale row는 일시적으로 남을 수 있다.
            log.warn("[NotificationScheduler] stale batch 회수 실패 — 이번 tick 계속 진행: error={}", e.getMessage(), e);
        }
    }

    /**
     * 테스트 및 오버라이드용: stale 기준 시각을 직접 지정하는 내부 메서드.
     * 프로덕션에서는 {@link #recoverStaleBatches()} 사용.
     *
     * @param threshold 이 시각 이전에 startedAt인 RUNNING batch를 FAILED로 전환
     */
    void recoverStaleBatches(Instant threshold) {
        List<NotificationBatch> staleBatches = loadBatchPort.findStaleRunning(threshold);
        if (staleBatches.isEmpty()) {
            return;
        }
        log.warn("[NotificationScheduler] stale RUNNING batch {}건 감지 — FAILED 처리", staleBatches.size());
        for (NotificationBatch stale : staleBatches) {
            stale.fail(0, 0);
            safeUpdateBatch(stale);
            log.warn("[NotificationScheduler] stale batch FAILED: batchId={}, startedAt={}",
                    stale.getId(), stale.getStartedAt());
        }
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
