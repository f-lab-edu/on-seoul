package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TriggerType;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 시점 기반 알림 트리거 스케줄러 (모델 B — OPEN_DAY / BEFORE_RECEIPT_D1 / DEADLINE_DDAY).
 *
 * <p><b>스케줄러 분리 결정</b>: CHANGE 배치({@link NotificationScheduler})는
 * {@code EmbeddingSyncCompletedEvent} 이벤트 기반이라 "그 직후 단계"로 동기 합류시키기 어렵다.
 * 따라서 별도 {@code @Scheduled} cron 으로 분리하고, CHANGE 수집/임베딩/알림 파이프라인이
 * 충분히 끝난 뒤(기본 09:30) 실행되도록 시각을 늦춰 <b>CHANGE 가 먼저 dispatch 를 선점</b>하게 한다.
 * 이 실행 순서가 cross-trigger dedup(CHANGE 우선)의 1차 보장이다.
 *
 * <p><b>dedup</b>:
 * <ul>
 *   <li>시점-vs-시점: {@code saveScheduledIfAbsent} 가 부분 unique 인덱스
 *       {@code uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date)} 로 강제 — 하루 1건.</li>
 *   <li>CHANGE-vs-시점: <b>best-effort</b>. CHANGE dispatch 는 service_id=NULL 이고 여러 service 를
 *       payload 에 묶으므로 DB 제약으로 cross dedup 을 강제할 수 없다. 실행 순서(CHANGE 먼저)로
 *       완화하며, 같은 service 에 대해 같은 날 CHANGE 알림과 시점 알림이 모두 나갈 수 있는
 *       경계 케이스가 이론적으로 존재한다(아래 [한계] 참조).</li>
 * </ul>
 *
 * <p>[한계] CHANGE 와 시점 알림이 같은 날 같은 service 에 중복 발송될 수 있다.
 * 정확한 cross dedup 을 하려면 CHANGE dispatch 에도 service_id 단위 발행(또는 dispatch_target
 * 연관 테이블)이 필요하나, 이는 이번 범위(시점 3종 추가) 대비 변경 폭이 과대하여 보류했다
 * (migration 11 [핵심 설계 결정] 옵션 (b) 참조).
 *
 * <p>처리 흐름(구독 청크 순회, service 단위):
 * <ol>
 *   <li>구독 filter 파싱 → 3개 트리거 조회(status 필터 무시, 지역/카테고리/키워드만)</li>
 *   <li>매칭 service 마다 {@code saveScheduledIfAbsent} 로 PENDING dispatch 멱등 INSERT</li>
 *   <li>INSERT 성공분만 템플릿 생성(trigger_type 전달) → 콘텐츠 조립 → 푸시 발송</li>
 *   <li>결과를 dispatch 에 기록(SUCCESS/FAILED). last_notified_at 커서는 건드리지 않는다.</li>
 * </ol>
 */
@Slf4j
@Component
public class ScheduledTriggerScheduler {

    static final int SUBSCRIPTION_CHUNK_SIZE = 100;

    private final LoadSubscriptionPort loadSubscriptionPort;
    private final LoadScheduledTriggerPort loadScheduledTriggerPort;
    private final SubscriptionFilterParserPort filterParser;
    private final LoadUserContactPort loadUserContactPort;
    private final TemplateGenerationPort templateGenerationPort;
    private final PushNotificationPort pushNotificationPort;
    private final NotificationContentSerializerPort contentSerializer;
    private final SaveBatchPort saveBatchPort;
    private final NotificationTxHelper txHelper;
    private final Clock clock;

    /** 중복 실행 방지. */
    private final AtomicBoolean running = new AtomicBoolean(false);

    public ScheduledTriggerScheduler(
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadScheduledTriggerPort loadScheduledTriggerPort,
            final SubscriptionFilterParserPort filterParser,
            final LoadUserContactPort loadUserContactPort,
            final TemplateGenerationPort templateGenerationPort,
            final PushNotificationPort pushNotificationPort,
            final NotificationContentSerializerPort contentSerializer,
            final SaveBatchPort saveBatchPort,
            final NotificationTxHelper txHelper,
            final Clock clock) {
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadScheduledTriggerPort = loadScheduledTriggerPort;
        this.filterParser = filterParser;
        this.loadUserContactPort = loadUserContactPort;
        this.templateGenerationPort = templateGenerationPort;
        this.pushNotificationPort = pushNotificationPort;
        this.contentSerializer = contentSerializer;
        this.saveBatchPort = saveBatchPort;
        this.txHelper = txHelper;
        this.clock = clock;
    }

    /**
     * 매일 09:30(KST 가정은 배포 타임존에 따름)에 시점 트리거 알림을 발송한다.
     * CHANGE 배치보다 늦게 실행되도록 시각을 늦춘다(실행 순서 = cross dedup 1차 보장).
     */
    @Scheduled(cron = "${notification.scheduled-trigger.cron:0 30 9 * * *}")
    public void run() {
        if (!running.compareAndSet(false, true)) {
            log.warn("[ScheduledTriggerScheduler] 이미 실행 중 — 무시");
            return;
        }
        try {
            // dispatch_date / today 기준: UTC 달력 날짜 (어댑터의 [d, d+1) 구간도 UTC 기준).
            processAll(LocalDate.now(clock.withZone(ZoneOffset.UTC)));
        } catch (Exception e) {
            log.error("[ScheduledTriggerScheduler] 실행 중 예외: {}", e.getMessage(), e);
        } finally {
            running.set(false);
        }
    }

    /** 패키지 가시성: 테스트에서 today 를 주입해 호출한다. */
    void processAll(LocalDate today) {
        log.debug("[ScheduledTriggerScheduler] 시점 트리거 배치 시작: today={}", today);
        int sent = 0;
        int skipped = 0;

        long afterId = 0L;
        while (true) {
            List<NotificationSubscription> chunk;
            try {
                chunk = loadSubscriptionPort.loadChunk(afterId, SUBSCRIPTION_CHUNK_SIZE);
            } catch (Exception e) {
                log.error("[ScheduledTriggerScheduler] 구독 청크 조회 실패 — 중단: afterId={}, error={}",
                        afterId, e.getMessage(), e);
                break;
            }
            for (NotificationSubscription sub : chunk) {
                try {
                    int[] r = processSubscription(sub, today);
                    sent += r[0];
                    skipped += r[1];
                } catch (Exception e) {
                    log.warn("[ScheduledTriggerScheduler] 구독 처리 실패 — 건너뜀: subscriptionId={}, error={}",
                            sub.getId(), e.getMessage());
                }
            }
            if (chunk.size() < SUBSCRIPTION_CHUNK_SIZE) {
                break;
            }
            afterId = chunk.get(chunk.size() - 1).getId();
        }
        log.info("[ScheduledTriggerScheduler] 시점 트리거 배치 완료: sent={}, skippedOrDup={}", sent, skipped);
    }

    /** @return [sent, skipped] 카운트. */
    private int[] processSubscription(NotificationSubscription sub, LocalDate today) {
        SubscriptionFilter filter = filterParser.parse(sub.getFilter());
        int sent = 0;
        int skipped = 0;

        // 트리거 3종 — 각 service 단위로 발행. 같은 service 가 두 트리거에 겹치면
        // uq_nd_scheduled_dedup 이 (subscription_id, service_id, dispatch_date) 로 1건만 허용한다.
        for (TriggerMatch tm : List.of(
                new TriggerMatch(TriggerType.OPEN_DAY,
                        loadScheduledTriggerPort.loadOpeningToday(filter, today)),
                new TriggerMatch(TriggerType.BEFORE_RECEIPT_D1,
                        loadScheduledTriggerPort.loadReceiptStartTomorrow(filter, today)),
                new TriggerMatch(TriggerType.DEADLINE_DDAY,
                        loadScheduledTriggerPort.loadDeadlineToday(filter, today)))) {
            for (ScheduledServiceMatch match : tm.matches()) {
                if (dispatchOne(sub, tm.triggerType(), match, today)) {
                    sent++;
                } else {
                    skipped++;
                }
            }
        }
        return new int[]{sent, skipped};
    }

    /**
     * service 1건 → dispatch 1건 발행 + 발송.
     *
     * @return 발송 성공 시 true, dedup 으로 skip 또는 발송 실패 시 false.
     */
    private boolean dispatchOne(NotificationSubscription sub, TriggerType triggerType,
                                ScheduledServiceMatch match, LocalDate today) {
        // batch_id 는 NOT NULL + FK(notification_batch) + uq_nd_batch_subscription(batch_id, subscription_id).
        // 한 구독이 같은 run 에서 service N건을 발행하므로 (batch_id, subscription_id) 충돌을 피하려면
        // service-dispatch 마다 별도 batch row 가 필요하다(migration 11 [batch 운용 규칙]).
        // 시점 dispatch 의 진짜 dedup 키는 uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date) 다.
        NotificationBatch batch = saveBatchPort.insertRunning(NotificationBatch.start());
        NotificationDispatch dispatch = NotificationDispatch.createScheduled(
                batch.getId(), sub.getId(), triggerType, match.serviceId(), today);

        Optional<NotificationDispatch> inserted = txHelper.saveScheduledIfAbsent(dispatch);
        if (inserted.isEmpty()) {
            // 이미 그날 같은 구독·서비스에 시점 dispatch 발행됨 → 방금 만든 빈 batch 는 SUCCESS(0건)로 마감.
            // [한계/보류] dedup-skip 케이스마다 빈 notification_batch row 가 누적된다.
            //   이를 피하려면 batch 생성 전에 (subscription_id, service_id, dispatch_date) 존재 여부를
            //   선조회하는 새 포트(LoadDispatchPort.existsScheduled...)가 필요한데, 부분 unique 인덱스
            //   술어를 그대로 반영한 쿼리 + 어댑터 + 테스트 추가가 필요해 이번 범위 대비 변경 폭이 크다.
            //   빈 batch 는 SUCCESS(0/0)로 마감되어 무해(에러 아님)하므로 이번 라운드는 현행 유지한다.
            batch.complete(0, 0);
            safeUpdateBatch(batch);
            return false;
        }
        return finishDispatch(sub, triggerType, match, inserted.get(), batch);
    }

    private boolean finishDispatch(NotificationSubscription sub, TriggerType triggerType,
                                   ScheduledServiceMatch match, NotificationDispatch persisted,
                                   NotificationBatch batch) {
        // 템플릿 생성: 시점 트리거는 changes 빈 배열. trigger_type 을 함께 전달.
        NotificationTemplateRequest.ServiceChangeGroup group =
                new NotificationTemplateRequest.ServiceChangeGroup(
                        match.serviceId(), match.serviceName(), match.serviceUrl(), match.imageUrl(),
                        match.placeName(), match.areaName(), match.serviceStatus(), match.targetInfo(),
                        match.receiptStartDt(), match.receiptEndDt(), List.of());
        TemplateResult template = templateGenerationPort.generate(
                new NotificationTemplateRequest(triggerType, List.of(group)));

        String name = (match.serviceName() != null && !match.serviceName().isBlank())
                ? match.serviceName() : match.serviceId();
        NotificationContent content = new NotificationContent(
                template.title(), template.summary(),
                List.of(new NotificationContent.ServiceCard(
                        name, match.serviceStatus(), match.areaName(), match.placeName(),
                        match.targetInfo(), match.receiptStartDt(), match.receiptEndDt(),
                        match.serviceUrl(), match.imageUrl(), List.of())));

        persisted.assignPayload(contentSerializer.serialize(content));

        UserContact recipient = loadUserContactPort.loadContact(sub.getUserId())
                .orElseGet(() -> new UserContact(sub.getUserId(), null, null));

        boolean ok;
        try {
            pushNotificationPort.send(recipient, content, persisted.getId(), sub.getChannels());
            // [한계] send 성공 후 txBScheduledSuccess 가 실패하면 dispatch 가 PENDING 으로 잔존해
            //   재시도 스케줄러가 재발송할 수 있다(at-least-once). 기존 CHANGE 경로(txBSuccess)와
            //   동일한 한계이므로 회귀가 아니다 — 정확한 exactly-once 보장은 별도 outbox 설계가 필요하다.
            txHelper.txBScheduledSuccess(persisted, template.title(), template.summary(), template.source());
            ok = true;
        } catch (RuntimeException pushEx) {
            txHelper.txBScheduledFailure(persisted,
                    template.title(), template.summary(), template.source(), pushEx.getMessage());
            ok = false;
        }
        batch.complete(ok ? 1 : 0, ok ? 0 : 1);
        safeUpdateBatch(batch);
        return ok;
    }

    private void safeUpdateBatch(NotificationBatch batch) {
        try {
            saveBatchPort.update(batch);
        } catch (Exception e) {
            log.warn("[ScheduledTriggerScheduler] batch UPDATE 실패: batchId={}, error={}",
                    batch.getId(), e.getMessage());
        }
    }

    private record TriggerMatch(TriggerType triggerType, List<ScheduledServiceMatch> matches) {}
}
