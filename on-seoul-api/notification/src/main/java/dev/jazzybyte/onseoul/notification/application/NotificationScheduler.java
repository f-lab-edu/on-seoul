package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplate;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * ADR-0004 per-batch 알림 스케줄러.
 *
 * <p>트리거: {@link EmbeddingSyncCompletedEvent} — 임베딩 동기화 완료 시 1회만 실행.
 * 처리 순서는 수집 → 임베딩 동기화 → 알림 이다. 수집 완료(CollectionCompletedEvent) 후
 * EmbeddingSyncWorker가 임베딩 동기화를 끝내고 EmbeddingSyncCompletedEvent를 발행하면
 * {@link #onEmbeddingSyncCompleted(EmbeddingSyncCompletedEvent)}가 비동기로 기동한다.
 * 알림은 임베딩 동기화 단계 완료 후에만 실행된다(임베딩 실패해도 best-effort로 진행).
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
    private final NotificationContentSerializerPort contentSerializer;
    private final MeterRegistry meterRegistry;
    private final long staleThresholdMs;

    /** 중복 실행 방지 플래그. 이벤트가 연속으로 두 번 오더라도 한 번만 처리한다. */
    private final AtomicBoolean running = new AtomicBoolean(false);

    public NotificationScheduler(
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadUserContactPort loadUserContactPort,
            final TemplateGenerationPort templateGenerationPort,
            final PushNotificationPort pushNotificationPort,
            final SaveBatchPort saveBatchPort,
            final LoadBatchPort loadBatchPort,
            final NotificationTxHelper txHelper,
            final NotificationContentSerializerPort contentSerializer,
            final MeterRegistry meterRegistry,
            @Value("${notification.scheduler.stale-threshold-ms:600000}") final long staleThresholdMs) {
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadUserContactPort = loadUserContactPort;
        this.templateGenerationPort = templateGenerationPort;
        this.pushNotificationPort = pushNotificationPort;
        this.saveBatchPort = saveBatchPort;
        this.loadBatchPort = loadBatchPort;
        this.txHelper = txHelper;
        this.contentSerializer = contentSerializer;
        this.meterRegistry = meterRegistry;
        this.staleThresholdMs = staleThresholdMs;
    }

    /**
     * 임베딩 동기화 완료 이벤트 수신 → 알림 배치 실행.
     *
     * <p>{@code @Async}로 호출 스레드를 블로킹하지 않는다.
     * 이미 실행 중이면 이벤트를 무시한다 (일 1회 수집이므로 정상적으로는 중복이 없다).
     */
    @Async
    @EventListener
    public void onEmbeddingSyncCompleted(EmbeddingSyncCompletedEvent event) {
        if (!running.compareAndSet(false, true)) {
            log.warn("[NotificationScheduler] 이미 실행 중 — 이벤트 무시");
            return;
        }
        try {
            processAllSubscriptions();
        } finally {
            running.set(false);
        }
    }

    void processAllSubscriptions() {
        log.debug("[NotificationScheduler] 배치 실행 시작");

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
        // 하나의 구독 필터가 여러 service_id에 매칭될 수 있으므로 service_id 단위로 그룹핑한다.
        List<NotificationTemplateRequest.ServiceChangeGroup> groups = toServiceGroups(changes);
        TemplateResult template = templateGenerationPort.generate(
                new NotificationTemplateRequest(groups));

        Counter.builder("notification.template.source")
                .tag("source", template.source().name())
                .register(meterRegistry)
                .increment();

        // 사실 데이터(서비스 카드)는 결정적으로 조립한다. AI는 summary만 생성한다.
        NotificationContent content = new NotificationContent(
                template.title(), template.summary(), toServiceCards(groups));

        // 발송 직전: content를 JSON 직렬화하여 dispatch에 보관(재시도 무손실 복원).
        // 직렬화 자체는 어댑터/매퍼 계층(contentSerializer) 책임 — 도메인/스케줄러는 raw String만 다룬다.
        dispatch.assignPayload(contentSerializer.serialize(content));

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
                    content,
                    dispatch.getId(),
                    sub.getChannels());

            try {
                txHelper.txBSuccess(dispatch, sub, batch,
                        template.title(), template.summary(), template.source());
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
                        template.title(), template.summary(), template.source(),
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

    /**
     * 변경 목록을 service_id 단위로 그룹핑한다.
     * {@link LinkedHashMap}으로 입력 순서(= changed_at asc)를 보존한다.
     * 각 그룹의 메타(serviceName 등)는 그 서비스의 첫 ServiceChange에서 가져온다.
     */
    private List<NotificationTemplateRequest.ServiceChangeGroup> toServiceGroups(List<ServiceChange> changes) {
        LinkedHashMap<String, List<ServiceChange>> byServiceId = new LinkedHashMap<>();
        for (ServiceChange c : changes) {
            byServiceId.computeIfAbsent(c.serviceId(), k -> new ArrayList<>()).add(c);
        }
        return byServiceId.values().stream()
                .map(group -> {
                    ServiceChange first = group.get(0);
                    List<NotificationTemplateRequest.ChangeItem> items = group.stream()
                            .map(c -> new NotificationTemplateRequest.ChangeItem(
                                    c.changeType(), c.fieldName(), c.oldValue(), c.newValue()))
                            .toList();
                    return new NotificationTemplateRequest.ServiceChangeGroup(
                            first.serviceId(), first.serviceName(), first.serviceUrl(), first.imageUrl(),
                            first.placeName(), first.areaName(), first.serviceStatus(), first.targetInfo(),
                            first.receiptStartDt(), first.receiptEndDt(), items);
                })
                .toList();
    }

    /**
     * 그룹 메타를 결정적 {@link NotificationContent.ServiceCard}로 변환한다.
     * 변경 라인의 label은 {@link NotificationTemplate#fieldLabel(String)}로 한글 매핑한다
     * (camelCase field_name 그대로 노출 금지).
     */
    private List<NotificationContent.ServiceCard> toServiceCards(
            List<NotificationTemplateRequest.ServiceChangeGroup> groups) {
        return groups.stream()
                .map(g -> {
                    List<NotificationContent.ChangeLine> lines = g.changes().stream()
                            .map(c -> new NotificationContent.ChangeLine(
                                    NotificationTemplate.fieldLabel(c.fieldName()),
                                    c.oldValue(), c.newValue()))
                            .toList();
                    String name = (g.serviceName() != null && !g.serviceName().isBlank())
                            ? g.serviceName() : g.serviceId();
                    // serviceId 는 payload 내부 식별자(cross-trigger dedup 선조회용)로만 보관한다.
                    // Knock wire/AI 요청에는 노출하지 않는다(toServiceMap/TemplateAgentDtoMapper 미반영).
                    return new NotificationContent.ServiceCard(
                            g.serviceId(), name, g.serviceStatus(), g.areaName(), g.placeName(),
                            g.targetInfo(), g.receiptStartDt(), g.receiptEndDt(),
                            g.serviceUrl(), g.imageUrl(), lines);
                })
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
