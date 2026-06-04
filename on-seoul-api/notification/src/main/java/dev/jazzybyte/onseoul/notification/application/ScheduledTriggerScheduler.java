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
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
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
 * <p><b>dedup (모두 batch 생성 전 선조회 — 빈 batch 미생성)</b>:
 * <ul>
 *   <li>CHANGE-vs-시점: dispatch 생성 전 {@code existsChangeDispatchForServiceToday} 로
 *       "오늘 같은 구독의 CHANGE payload 가 이 service 를 이미 커버하는가"를 JSONB containment
 *       ({@code notification_payload->'services' @> '[{"serviceId":"X"}]'}, idx_nd_change_crossdedup)
 *       로 확인해, 커버하면 시점 발행을 skip 한다(CHANGE 우선). 이로써 cross-trigger dedup 을
 *       <b>DB 조회로 실제 강제</b>한다(과거 "실행 순서 best-effort"에서 강화).</li>
 *   <li>시점-vs-시점: dispatch 생성 전 {@code existsScheduledDispatch}
 *       (subscription_id, service_id, dispatch_date) 로 존재 확인 후 없을 때만 발행한다.
 *       부분 unique 인덱스 {@code uq_nd_scheduled_dedup} 의 ON CONFLICT 멱등
 *       ({@code saveScheduledIfAbsent})은 race-safety 백업으로 유지한다.</li>
 * </ul>
 *
 * <p>두 dedup 선조회를 batch 생성 <b>이전</b>으로 옮겨, skip 케이스에서 batch 를 만들지 않는다
 * → 빈 {@code notification_batch} row 가 누적되지 않는다(이전 한계 해소).
 *
 * <p><b>전제</b>: CHANGE dispatch 가 dispatch_date(UTC today)와 payload.services[].serviceId 를
 * 채운다(NotificationScheduler/NotificationTxHelper, migration 12). 두 알림 경로의 today 는 동일
 * UTC Clock({@code NotificationClockConfig}) 기준이라 dispatch_date 가 일치한다.
 *
 * <p>처리 흐름(구독 청크 순회, service 단위):
 * <ol>
 *   <li>구독 filter 파싱 → 3개 트리거 조회(status 필터 무시, 지역/카테고리/키워드만)</li>
 *   <li>매칭 service 마다 (a) CHANGE cross-dedup, (b) 시점-시점 dedup 선조회 — 둘 중 하나라도
 *       히트하면 batch 생성 없이 skip</li>
 *   <li>둘 다 통과한 service 만 batch INSERT → {@code saveScheduledIfAbsent} 로 PENDING dispatch
 *       멱등 INSERT(race-safety 백업)</li>
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
    private final LoadDispatchPort loadDispatchPort;
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
            final LoadDispatchPort loadDispatchPort,
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
        this.loadDispatchPort = loadDispatchPort;
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
     * 매일 09:30 UTC(= 18:30 KST)에 시점 트리거 알림을 발송한다.
     * JVM 기본 존이 OnSeoulApiApplication.init()에서 UTC로 강제되므로 zone 미지정 cron은 UTC 기준이다.
     * today/dispatch_date도 UTC 달력 기준이라 발화 존과 정합된다.
     * CHANGE 배치(CollectionScheduler 08:00 KST = 23:00 UTC 전일)보다 늦게 실행되도록 시각을 늦춘다
     * (실행 순서 = cross dedup 1차 보장).
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

    /**
     * 수동 1회 실행(임시 관리 API용). {@code @Scheduled} 진입점({@link #run()})과 동일한
     * {@code running} 가드를 공유하므로 스케줄/수동 호출 간 중복 실행을 유발하지 않는다.
     * 이미 실행 중이면 {@link ManualRunResult#SKIPPED_ALREADY_RUNNING}을 반환한다.
     *
     * @return 실행 여부({@link ManualRunResult}). {@code processAll}은 sent/skipped 집계를
     *         {@link RunResult}로 반환하지만(테스트가 skip 집계를 단정), 수동 실행 API 는 발송 결과를
     *         {@code notification_batch}/{@code notification_dispatch} row 로 기록하므로 카운트를
     *         외부로 노출하지 않고 실행 여부만 돌려준다.
     */
    public ManualRunResult runManually() {
        if (!running.compareAndSet(false, true)) {
            log.warn("[ScheduledTriggerScheduler] 수동 실행 요청 — 이미 실행 중이므로 skip");
            return ManualRunResult.SKIPPED_ALREADY_RUNNING;
        }
        try {
            processAll(LocalDate.now(clock.withZone(ZoneOffset.UTC)));
            return ManualRunResult.RAN;
        } finally {
            running.set(false);
        }
    }

    /** 수동 실행 결과. */
    public enum ManualRunResult {
        RAN,
        SKIPPED_ALREADY_RUNNING
    }

    /**
     * 시점 트리거 배치 1회 실행 결과 집계.
     *
     * @param sent    발송 성공한 dispatch 수.
     * @param skipped dedup(cross/시점-시점) 또는 발송 실패로 발행되지 않은 service 수.
     */
    record RunResult(int sent, int skipped) {}

    /**
     * 패키지 가시성: 테스트에서 today 를 주입해 호출한다.
     *
     * @return 이번 배치의 sent/skipped 집계({@link RunResult}). {@link #run()}/{@link #runManually()}
     *         는 이 값을 로그로만 남기지만, 테스트는 반환값으로 skip 집계를 직접 단정한다.
     */
    RunResult processAll(LocalDate today) {
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
        return new RunResult(sent, skipped);
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
     * <p>batch 생성 <b>전</b>에 두 dedup 을 선조회한다 → skip 시 빈 batch 를 만들지 않는다:
     * <ol>
     *   <li>CHANGE cross-dedup: 오늘 같은 구독의 CHANGE 가 이 service 를 이미 커버하면 skip(CHANGE 우선).</li>
     *   <li>시점-시점 dedup: 오늘 같은 구독·서비스에 시점 dispatch 가 이미 있으면 skip.</li>
     * </ol>
     * 둘 다 통과하면 batch INSERT 후 {@code saveScheduledIfAbsent}(ON CONFLICT 멱등 = race-safety 백업)로 발행한다.
     *
     * @return 발송 성공 시 true, dedup 으로 skip 또는 발송 실패 시 false.
     */
    private boolean dispatchOne(NotificationSubscription sub, TriggerType triggerType,
                                ScheduledServiceMatch match, LocalDate today) {
        // 1) CHANGE↔시점 cross-dedup: 오늘 같은 구독의 CHANGE payload 가 이 service 를 이미 커버하면 skip.
        if (loadDispatchPort.existsChangeDispatchForServiceToday(sub.getId(), match.serviceId(), today)) {
            return false;
        }
        // 2) 시점-시점 dedup: 오늘 같은 구독·서비스에 시점 dispatch 가 이미 있으면 skip.
        if (loadDispatchPort.existsScheduledDispatch(sub.getId(), match.serviceId(), today)) {
            return false;
        }

        // batch_id 는 NOT NULL + FK(notification_batch) + uq_nd_batch_subscription(batch_id, subscription_id).
        // 한 구독이 같은 run 에서 service N건을 발행하므로 (batch_id, subscription_id) 충돌을 피하려면
        // service-dispatch 마다 별도 batch row 가 필요하다(migration 11 [batch 운용 규칙]).
        // 선조회를 통과한 service 만 여기 도달하므로 빈 batch 는 만들어지지 않는다.
        NotificationBatch batch = saveBatchPort.insertRunning(NotificationBatch.start());
        NotificationDispatch dispatch = NotificationDispatch.createScheduled(
                batch.getId(), sub.getId(), triggerType, match.serviceId(), today);

        Optional<NotificationDispatch> inserted = txHelper.saveScheduledIfAbsent(dispatch);
        if (inserted.isEmpty()) {
            // race-safety 백업: 선조회 이후 동시 INSERT 로 uq_nd_scheduled_dedup 충돌(드묾).
            // 이 경우에만 빈 batch 가 생기며 SUCCESS(0/0)로 마감한다 — 정상 경로에선 발생하지 않는다.
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
                        match.serviceId(), name, match.serviceStatus(), match.areaName(), match.placeName(),
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
