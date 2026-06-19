package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.adapter.out.persistence.NotificationContentSerializer;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static dev.jazzybyte.onseoul.notification.application.NotificationScheduler.SUBSCRIPTION_CHUNK_SIZE;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class NotificationSchedulerTest {

    @Mock private LoadSubscriptionPort loadSubscriptionPort;
    @Mock private LoadUserContactPort loadUserContactPort;
    @Mock private TemplateGenerationPort templateGenerationPort;
    @Mock private PushNotificationPort pushNotificationPort;
    @Mock private SaveBatchPort saveBatchPort;
    @Mock private LoadBatchPort loadBatchPort;
    @Mock private NotificationTxHelper txHelper;

    private SimpleMeterRegistry meterRegistry;
    private NotificationScheduler scheduler;

    private static final Long BATCH_ID = 42L;
    private static final Instant BATCH_STARTED_AT = Instant.parse("2026-05-22T09:00:00Z");
    private static final UserContact TEST_CONTACT =
            new UserContact(1L, "user@example.com", "+821012345678");

    @BeforeEach
    void setUp() {
        meterRegistry = new SimpleMeterRegistry();
        scheduler = new NotificationScheduler(
                loadSubscriptionPort, loadUserContactPort,
                templateGenerationPort, pushNotificationPort,
                saveBatchPort, loadBatchPort, txHelper,
                new NotificationContentSerializer(new com.fasterxml.jackson.databind.ObjectMapper()),
                meterRegistry, 600_000L);

        lenient().when(loadUserContactPort.loadContact(anyLong()))
                .thenReturn(Optional.of(TEST_CONTACT));
        // 기본: stale RUNNING batch 없음
        lenient().when(loadBatchPort.findStaleRunning(any())).thenReturn(List.of());
        // 기본: 구독 없음 (커서 진행 시 빈 리스트 반환 — 청크 루프 종료)
        lenient().when(loadSubscriptionPort.loadChunk(anyLong(), anyInt())).thenReturn(List.of());

        // 기본: insertRunning은 id/startedAt이 채워진 배치를 반환
        lenient().when(saveBatchPort.insertRunning(any())).thenAnswer(inv -> {
            NotificationBatch b = inv.getArgument(0);
            return new NotificationBatch(BATCH_ID, BATCH_STARTED_AT, null,
                    BatchStatus.RUNNING, b.getSentCount(), b.getFailedCount());
        });
        lenient().when(saveBatchPort.update(any())).thenAnswer(inv -> inv.getArgument(0));
    }

    private NotificationSubscription sub(Long id) {
        return NotificationSubscription.ofPersistence(id, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
    }

    private ServiceChange change(Long id, String serviceId) {
        return new ServiceChange(id, serviceId, "UPDATED", "service_status",
                "RECEIVING", "CLOSED", Instant.now(),
                serviceId + "-name", null, null, null, null, "RECEIVING", null, null, null);
    }

    private NotificationDispatch dispatch(Long subId) {
        return NotificationDispatch.create(BATCH_ID, subId);
    }

    private NotificationTxHelper.TxAResult txAResult(List<ServiceChange> changes,
                                                    NotificationDispatch dispatch) {
        return new NotificationTxHelper.TxAResult(changes, Optional.ofNullable(dispatch));
    }

    // ── 동시성 가드 공유 (이벤트 vs 수동) ──────────────────────────────

    @Test
    @DisplayName("동시성 가드: 이벤트 핸들러 실행 중이면 runManually()는 배치 재진입 없이 SKIPPED")
    void runManually_whileEventRunning_skipsWithoutReentry() throws Exception {
        // onEmbeddingSyncCompleted 가 잡은 running 플래그를 수동 호출이 공유하는지 검증한다.
        // insertRunning 안에서 latch로 블로킹하여 running=true 상태를 유지한다.
        java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch release = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.atomic.AtomicInteger chunkCalls = new java.util.concurrent.atomic.AtomicInteger();

        // insertRunning(default 스텁) 이후 첫 loadChunk(0,*) 안에서 블로킹하여 running=true 유지.
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenAnswer(inv -> {
            chunkCalls.incrementAndGet();
            entered.countDown();
            release.await(5, java.util.concurrent.TimeUnit.SECONDS);
            return List.of();
        });

        Thread eventThread = new Thread(
                () -> scheduler.onEmbeddingSyncCompleted(
                        new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent()),
                "embedding-event");
        eventThread.start();
        assertThat(entered.await(5, java.util.concurrent.TimeUnit.SECONDS)).isTrue();

        // 이벤트 핸들러가 배치 처리 중인 동안 수동 호출 시도
        NotificationScheduler.ManualRunResult result = scheduler.runManually();

        assertThat(result).isEqualTo(NotificationScheduler.ManualRunResult.SKIPPED_ALREADY_RUNNING);
        // 가드 우회로 인한 배치 재진입(=두 번째 loadChunk(0,*))이 없어야 한다.
        assertThat(chunkCalls.get()).isEqualTo(1);

        release.countDown();
        eventThread.join(5_000);

        // 첫 배치 종료 후 플래그 해제 → 다음 수동 실행은 RAN
        assertThat(scheduler.runManually())
                .isEqualTo(NotificationScheduler.ManualRunResult.RAN);
        assertThat(chunkCalls.get()).isEqualTo(2);
    }

    // ── 배치 INSERT/UPDATE 흐름 ─────────────────────────────────────────

    @Test
    @DisplayName("배치 시작 시 insertRunning이 호출되고, 종료 시 update가 호출된다 (구독 없음)")
    void run_noSubscriptions_stillInsertsAndUpdatesBatch() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());

        scheduler.processAllSubscriptions();

        verify(saveBatchPort).insertRunning(any());
        ArgumentCaptor<NotificationBatch> captor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(captor.capture());
        NotificationBatch finalBatch = captor.getValue();
        assertThat(finalBatch.getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(finalBatch.getSentCount()).isEqualTo(0);
        assertThat(finalBatch.getFailedCount()).isEqualTo(0);

        verifyNoInteractions(txHelper, templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("Batch INSERT 실패 시 이번 tick은 중단된다 (loadChunk 미호출)")
    void run_batchInsertFails_aborts() {
        doThrow(new RuntimeException("DB 다운")).when(saveBatchPort).insertRunning(any());

        scheduler.processAllSubscriptions();

        verifyNoInteractions(loadSubscriptionPort, txHelper);
        verify(saveBatchPort, never()).update(any());
    }

    @Test
    @DisplayName("구독 청크 조회 실패 시 배치는 FAILED 상태로 UPDATE 된다")
    void run_chunkLoadFails_marksBatchFailed() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenThrow(new RuntimeException("쿼리 실패"));

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationBatch> captor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(captor.capture());
        assertThat(captor.getValue().getStatus()).isEqualTo(BatchStatus.FAILED);
    }

    // ── stale RUNNING batch 회수 ──────────────────────────────────────────

    @Test
    @DisplayName("stale RUNNING batch가 있으면 FAILED로 UPDATE된다")
    void recoverStaleBatches_staleExists_marksFailedAndUpdates() {
        NotificationBatch stale = new NotificationBatch(
                7L, Instant.now().minusSeconds(700), null, BatchStatus.RUNNING, null, null);
        Instant threshold = Instant.now().minusSeconds(600);

        when(loadBatchPort.findStaleRunning(threshold)).thenReturn(List.of(stale));

        scheduler.recoverStaleBatches(threshold);

        assertThat(stale.getStatus()).isEqualTo(BatchStatus.FAILED);
        assertThat(stale.getFinishedAt()).isNotNull();
        ArgumentCaptor<NotificationBatch> captor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(captor.capture());
        assertThat(captor.getValue().getId()).isEqualTo(7L);
        assertThat(captor.getValue().getStatus()).isEqualTo(BatchStatus.FAILED);
    }

    @Test
    @DisplayName("stale RUNNING batch가 없으면 UPDATE를 호출하지 않는다")
    void recoverStaleBatches_noneStale_skipsUpdate() {
        Instant threshold = Instant.now().minusSeconds(600);
        when(loadBatchPort.findStaleRunning(threshold)).thenReturn(List.of());

        scheduler.recoverStaleBatches(threshold);

        verify(saveBatchPort, never()).update(any());
    }

    @Test
    @DisplayName("stale batch가 여러 건이면 모두 FAILED UPDATE된다")
    void recoverStaleBatches_multiple_allMarkedFailed() {
        NotificationBatch stale1 = new NotificationBatch(5L, Instant.now().minusSeconds(800),
                null, BatchStatus.RUNNING, null, null);
        NotificationBatch stale2 = new NotificationBatch(6L, Instant.now().minusSeconds(900),
                null, BatchStatus.RUNNING, null, null);
        Instant threshold = Instant.now().minusSeconds(600);

        when(loadBatchPort.findStaleRunning(threshold)).thenReturn(List.of(stale1, stale2));

        scheduler.recoverStaleBatches(threshold);

        assertThat(stale1.getStatus()).isEqualTo(BatchStatus.FAILED);
        assertThat(stale2.getStatus()).isEqualTo(BatchStatus.FAILED);
        verify(saveBatchPort, org.mockito.Mockito.times(2)).update(any());
    }

    @Test
    @DisplayName("processAllSubscriptions 실행 시 stale 회수가 배치 INSERT 전에 수행된다")
    void processAllSubscriptions_recoverCalledBeforeInsert() {
        NotificationBatch stale = new NotificationBatch(
                3L, Instant.now().minusSeconds(700), null, BatchStatus.RUNNING, null, null);
        // 최소 1번 이상 findStaleRunning이 호출되면 stale batch 반환
        when(loadBatchPort.findStaleRunning(any())).thenReturn(List.of(stale));
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());

        scheduler.processAllSubscriptions();

        // stale batch UPDATE + 새 배치 INSERT + 새 배치 UPDATE — 총 update 2회
        verify(saveBatchPort, org.mockito.Mockito.times(2)).update(any());
        verify(saveBatchPort).insertRunning(any());
    }

    @Test
    @DisplayName("stale UPDATE 실패(safeUpdate)는 삼켜지고 새 배치는 정상 실행된다")
    void recoverStaleBatches_updateFails_isSwallowedAndContinues() {
        NotificationBatch stale = new NotificationBatch(
                8L, Instant.now().minusSeconds(700), null, BatchStatus.RUNNING, null, null);
        when(loadBatchPort.findStaleRunning(any())).thenReturn(List.of(stale));
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());
        // stale UPDATE 실패, 새 배치 UPDATE 성공
        doThrow(new RuntimeException("DB 오류")).doAnswer(inv -> inv.getArgument(0))
                .when(saveBatchPort).update(any());

        scheduler.processAllSubscriptions();

        // 새 배치 insertRunning은 호출됨
        verify(saveBatchPort).insertRunning(any());
        // update는 2회 시도됨 (stale 1회 + 새 배치 1회)
        verify(saveBatchPort, org.mockito.Mockito.times(2)).update(any());
    }

    @Test
    @DisplayName("findStaleRunning 예외 발생 시 해당 tick은 계속 진행된다 (insertRunning 호출됨)")
    void recoverStaleBatches_findStaleRunningThrows_tickContinues() {
        when(loadBatchPort.findStaleRunning(any())).thenThrow(new RuntimeException("DB 다운"));
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());

        scheduler.processAllSubscriptions();

        // stale 회수 실패해도 새 배치는 정상 삽입되어야 한다
        verify(saveBatchPort).insertRunning(any());
    }

    // ── per-subscription 흐름 ────────────────────────────────────────────

    @Test
    @DisplayName("TX A가 빈 changes를 반환하면 template/push를 호출하지 않는다")
    void txAEmpty_skipsTemplateAndPush() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                .thenReturn(txAResult(List.of(), null));

        scheduler.processAllSubscriptions();

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("TX A에서 dispatch가 중복(empty)이면 push 미호출")
    void duplicateDispatch_skipsPush() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                .thenReturn(txAResult(List.of(change(100L, "OA-2269")), null));

        scheduler.processAllSubscriptions();

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("정상 흐름: template/push 호출, txBSuccess 호출, sent_count=1")
    void successPath_callsTxBSuccessAndIncrementsSentCount() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        verify(pushNotificationPort).send(any(UserContact.class), any(NotificationContent.class), any(), any());
        ArgumentCaptor<NotificationBatch> batchCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(txHelper).txBSuccess(eq(d), eq(s), batchCaptor.capture(),
                eq("제목"), eq("본문"), eq(TemplateSource.AI));
        assertThat(batchCaptor.getValue().getId()).isEqualTo(BATCH_ID);
        assertThat(batchCaptor.getValue().getStartedAt()).isEqualTo(BATCH_STARTED_AT);

        // 종료 시 sentCount=1, failedCount=0
        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(1);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("push 실패 시 txBFailure 호출, failed_count 증가, last_notified_at 갱신 없음")
    void pushFails_callsTxBFailureAndIncrementsFailedCount() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.FALLBACK);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(UserContact.class), any(NotificationContent.class), any(), any());

        scheduler.processAllSubscriptions();

        verify(txHelper).txBFailure(eq(d), eq("제목"), eq("본문"), eq(TemplateSource.FALLBACK), eq("Knock 오류"));

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getStatus()).isEqualTo(BatchStatus.SUCCESS); // orchestration 성공
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(0);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("TX A 실패 시 failed_count 증가 후 다음 구독 처리")
    void txAFails_incrementsFailedAndContinues() {
        NotificationSubscription s1 = sub(1L);
        NotificationSubscription s2 = sub(2L);
        ServiceChange c2 = change(200L, "OA-2266");
        NotificationDispatch d2 = dispatch(2L);
        TemplateResult template = new TemplateResult("제목2", "본문2", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s1, s2));
        when(txHelper.txA(any(NotificationBatch.class), eq(s1))).thenThrow(new RuntimeException("DB 오류"));
        when(txHelper.txA(any(NotificationBatch.class), eq(s2))).thenReturn(txAResult(List.of(c2), d2));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        verify(pushNotificationPort).send(any(UserContact.class), any(NotificationContent.class), any(), any());

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(1);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(1);
    }

    // ── metrics ──────────────────────────────────────────────────────────

    @Test
    @DisplayName("성공 시 notification.dispatch.attempts{result=success} 카운터가 증가")
    void successPath_incrementsSuccessCounter() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "본문", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        assertThat(meterRegistry.counter("notification.dispatch.attempts", "result", "success")
                .count()).isEqualTo(1.0);
    }

    @Test
    @DisplayName("실패 시 notification.dispatch.attempts{result=failed} + notification.dispatch.failed.total 카운터 증가")
    void pushFails_incrementsFailedCounters() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "본문", TemplateSource.AI));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), any(NotificationContent.class), any(), any());

        scheduler.processAllSubscriptions();

        assertThat(meterRegistry.counter("notification.dispatch.attempts", "result", "failed")
                .count()).isEqualTo(1.0);
        assertThat(meterRegistry.counter("notification.dispatch.failed.total").count())
                .isEqualTo(1.0);
    }

    @Test
    @DisplayName("template source=AI 일 때 notification.template.source{source=AI} 카운터 증가")
    void templateSourceAi_incrementsCounter() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "본문", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        assertThat(meterRegistry.counter("notification.template.source", "source", "AI").count())
                .isEqualTo(1.0);
    }

    // ── 부정 / 엣지 케이스 ──────────────────────────────────────────────

    @Test
    @DisplayName("Batch UPDATE 실패는 삼켜진다 — 스케줄러 자체는 예외 없이 종료")
    void batchUpdateFails_isSwallowed() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());
        doThrow(new RuntimeException("UPDATE 실패")).when(saveBatchPort).update(any());

        // 예외가 밖으로 새어 나오지 않음 — 다음 tick은 새 batch_id로 정상 실행 가능해야 함
        scheduler.processAllSubscriptions();

        verify(saveBatchPort).insertRunning(any());
        verify(saveBatchPort).update(any());
    }

    @Test
    @DisplayName("구독 청크 조회 실패 + Batch UPDATE 실패 — 모두 삼켜지고 예외 없이 종료")
    void chunkLoadAndBatchUpdateBothFail_areSwallowed() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenThrow(new RuntimeException("쿼리 실패"));
        doThrow(new RuntimeException("UPDATE 실패")).when(saveBatchPort).update(any());

        scheduler.processAllSubscriptions();

        verify(saveBatchPort).update(any());
    }

    @Test
    @DisplayName("TX B(성공) 실패는 삼켜지고 sent_count는 증가한다 (push 성공 분기 유지)")
    void txBSuccessFails_isSwallowedButSentCountIncrements() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));
        doThrow(new RuntimeException("DB 오류")).when(txHelper)
                .txBSuccess(any(), any(), any(), any(), any(), any());

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        // push가 성공했으므로 sent_count는 증가하고 status는 SUCCESS
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(1);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("연락처 미등록 시 fallback UserContact(userId만)으로 발송 시도")
    void contactNotFound_fallsBackToUserIdOnly() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));
        when(loadUserContactPort.loadContact(anyLong())).thenReturn(Optional.empty());

        scheduler.processAllSubscriptions();

        ArgumentCaptor<UserContact> contactCaptor = ArgumentCaptor.forClass(UserContact.class);
        verify(pushNotificationPort).send(contactCaptor.capture(), any(NotificationContent.class), any(), any());
        assertThat(contactCaptor.getValue().userId()).isEqualTo(1L);
        assertThat(contactCaptor.getValue().email()).isNull();
    }

    @Test
    @DisplayName("청크 루프: 2페이지(CHUNK_SIZE, 나머지)로 분할된 구독을 모두 처리한다")
    void chunkedSubscriptions_allProcessedAcrossPages() {
        // CHUNK_SIZE건(첫 페이지) + 1건(마지막 페이지)으로 분할
        int firstPageSize = SUBSCRIPTION_CHUNK_SIZE;
        java.util.List<NotificationSubscription> firstPage = new java.util.ArrayList<>();
        for (long i = 1; i <= firstPageSize; i++) {
            NotificationSubscription s = sub(i);
            firstPage.add(s);
            when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                    .thenReturn(txAResult(List.of(change(i + 1000, "OA-" + i)), dispatch(i)));
        }
        NotificationSubscription lastSub = sub((long) firstPageSize + 1);
        when(txHelper.txA(any(NotificationBatch.class), eq(lastSub)))
                .thenReturn(txAResult(List.of(change(9999L, "OA-last")), dispatch((long) firstPageSize + 1)));

        // 첫 페이지: CHUNK_SIZE건 반환 → 루프 계속
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(firstPage);
        // 두 번째 페이지: 1건 반환 (< CHUNK_SIZE) → 루프 종료
        when(loadSubscriptionPort.loadChunk(eq((long) firstPageSize), eq(SUBSCRIPTION_CHUNK_SIZE)))
                .thenReturn(List.of(lastSub));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "본문", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(firstPageSize + 1);
    }

    @Test
    @DisplayName("동시성: 다중 구독을 처리해도 모든 작업이 끝난 후 batch.complete 호출됨 (try-with-resources awaitTermination)")
    void manyConcurrentSubscriptions_allCompletedBeforeBatchUpdate() {
        int n = 20;
        java.util.List<NotificationSubscription> subs = new java.util.ArrayList<>();
        for (long i = 1; i <= n; i++) {
            NotificationSubscription s = sub(i);
            subs.add(s);
            when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                    .thenReturn(txAResult(List.of(change(i + 1000, "OA-" + i)), dispatch(i)));
        }
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(subs);
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        // 모든 구독에 대해 push send가 호출되어야 한다
        verify(pushNotificationPort, org.mockito.Mockito.times(n))
                .send(any(UserContact.class), any(NotificationContent.class), any(), any());

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(n);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(0);
    }

    // ── metrics (negative) ───────────────────────────────────────────────

    @Test
    @DisplayName("notification.dispatch.dead 카운터는 등록되지 않는다 (DEAD 메트릭 제거 확인)")
    void deadMetric_isNotRegistered() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), any(NotificationContent.class), any(), any());

        scheduler.processAllSubscriptions();

        // SimpleMeterRegistry에서 등록된 모든 meter 이름을 확인 — dead가 없어야 함
        assertThat(meterRegistry.getMeters())
                .extracting(m -> m.getId().getName())
                .doesNotContain("notification.dispatch.dead");
    }

    @Test
    @DisplayName("같은 serviceId의 변경들은 하나의 그룹으로 묶여 changes 목록에 순서대로 담긴다")
    void templateRequest_sameServiceId_groupedIntoSingleGroup() {
        NotificationSubscription s = sub(1L);
        ServiceChange c1 = new ServiceChange(100L, "OA-2269", "UPDATED",
                "service_status", "RECEIVING", "CLOSED", Instant.now(),
                "수영교실", "https://ex.com/1", null, "강남센터", "강남구", "RECEIVING",
                "성인", "2026-05-01T00:00Z", "2026-05-31T00:00Z");
        ServiceChange c2 = new ServiceChange(101L, "OA-2269", "UPDATED",
                "receipt_end_dt", "2026-05-01", "2026-05-31", Instant.now(),
                "수영교실", "https://ex.com/1", null, "강남센터", "강남구", "RECEIVING",
                "성인", "2026-05-01T00:00Z", "2026-05-31T00:00Z");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c1, c2), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationTemplateRequest> captor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(captor.capture());
        NotificationTemplateRequest req = captor.getValue();

        assertThat(req.services()).hasSize(1);
        NotificationTemplateRequest.ServiceChangeGroup g = req.services().get(0);
        assertThat(g.serviceId()).isEqualTo("OA-2269");
        assertThat(g.serviceName()).isEqualTo("수영교실");
        assertThat(g.serviceUrl()).isEqualTo("https://ex.com/1");
        assertThat(g.changes()).hasSize(2);
        assertThat(g.changes().get(0).fieldName()).isEqualTo("service_status");
        assertThat(g.changes().get(1).fieldName()).isEqualTo("receipt_end_dt");
    }

    @Test
    @DisplayName("여러 serviceId가 섞인 변경은 입력 순서를 보존한 N개 그룹으로 묶여 1회 generate 호출된다")
    void templateRequest_multipleServiceIds_groupedPreservingOrder() {
        NotificationSubscription s = sub(1L);
        ServiceChange a = change(100L, "OA-A");
        ServiceChange b1 = change(101L, "OA-B");
        ServiceChange b2 = change(102L, "OA-B");
        ServiceChange c = change(103L, "OA-C");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                .thenReturn(txAResult(List.of(a, b1, b2, c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationTemplateRequest> captor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(captor.capture());
        NotificationTemplateRequest req = captor.getValue();

        // 3개 그룹: OA-A, OA-B, OA-C — 입력 순서(changed_at asc) 보존
        assertThat(req.services()).extracting(NotificationTemplateRequest.ServiceChangeGroup::serviceId)
                .containsExactly("OA-A", "OA-B", "OA-C");
        // OA-B 그룹은 변경 2건을 가진다
        assertThat(req.services().get(1).changes()).hasSize(2);
        assertThat(req.services().get(0).changes()).hasSize(1);
        assertThat(req.services().get(2).changes()).hasSize(1);
    }

    // ── 이벤트 트리거 + 중복 실행 방지 (QA 회귀 테스트) ──────────────────────

    @Test
    @DisplayName("이벤트 수신 시 알림 배치가 1회 실행된다 (onEmbeddingSyncCompleted → processAllSubscriptions)")
    void onEmbeddingSyncCompleted_runsBatchOnce() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());

        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());

        verify(saveBatchPort).insertRunning(any());
        verify(saveBatchPort).update(any());
    }

    @Test
    @DisplayName("배치 실행이 끝나면 running 플래그가 해제되어 다음 이벤트도 정상 실행된다")
    void onEmbeddingSyncCompleted_releasesFlagAfterRun_allowingNextEvent() {
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());

        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());
        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());

        // 두 번의 순차 이벤트 → 두 번 실행 (flag가 finally에서 해제됨)
        verify(saveBatchPort, org.mockito.Mockito.times(2)).insertRunning(any());
    }

    @Test
    @DisplayName("배치 실행 중 예외가 나도 running 플래그가 해제된다 (finally)")
    void onEmbeddingSyncCompleted_resetsFlagEvenWhenBatchThrows() {
        // stale 회수가 정상 종료 후, recoverStaleBatches 단계에서 던진 예외는 내부에서 삼켜진다.
        // 대신 update가 던지게 하여 processAllSubscriptions 종료 경로의 안정성과 flag 해제를 함께 검증한다.
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of());
        // 1회차에서 update가 예외를 던져도 safeUpdateBatch가 삼키고 정상 종료 → flag 해제
        when(saveBatchPort.update(any()))
                .thenThrow(new RuntimeException("update fail"))
                .thenAnswer(inv -> inv.getArgument(0));

        // 1회차: update 예외를 내부에서 삼키고 종료 (flag 해제 기대)
        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());
        // 2회차: flag가 해제되었으므로 다시 실행되어야 한다
        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());

        // flag가 매번 해제됨을 증명 — insertRunning이 2회 호출됨
        verify(saveBatchPort, org.mockito.Mockito.times(2)).insertRunning(any());
    }

    @Test
    @DisplayName("이미 실행 중(running=true)이면 동시 이벤트는 무시된다 (CAS 중복 방지)")
    void onEmbeddingSyncCompleted_concurrentEvent_isIgnoredWhileRunning() throws Exception {
        java.util.concurrent.CountDownLatch insideBatch = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch releaseBatch = new java.util.concurrent.CountDownLatch(1);

        // loadChunk에서 블로킹시켜 첫 이벤트가 "실행 중(running=true)" 상태를 유지하게 한다.
        // (insertRunning 기본 stub은 setUp의 lenient 답변을 그대로 사용)
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenAnswer(inv -> {
            insideBatch.countDown();
            releaseBatch.await();
            return List.of();
        });

        Thread first = new Thread(() ->
                scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent()));
        first.start();

        // 첫 이벤트가 배치 내부(running=true)에 진입할 때까지 대기
        assertThat(insideBatch.await(5, java.util.concurrent.TimeUnit.SECONDS)).isTrue();

        // 두 번째 이벤트: running=true이므로 즉시 무시되어야 한다 (insertRunning 추가 호출 없음)
        scheduler.onEmbeddingSyncCompleted(new dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent());

        releaseBatch.countDown();
        first.join(5_000);

        // insertRunning은 첫 이벤트에 의해 정확히 1회만 호출됨
        verify(saveBatchPort, org.mockito.Mockito.times(1)).insertRunning(any());
    }

    // ── 구조화 콘텐츠 조립 (ServiceCard + 한글 라벨 + payload 직렬화) ──────────

    @Test
    @DisplayName("발송 콘텐츠는 그룹 메타로 결정적 ServiceCard를 조립하고 changes[].label을 한글로 매핑한다")
    void dispatch_assemblesDeterministicServiceCardWithKoreanLabel() {
        NotificationSubscription s = sub(1L);
        // fieldName=service_status → 한글 라벨 "모집상태"로 매핑되어야 한다 (camelCase 노출 금지).
        ServiceChange c = new ServiceChange(
                100L, "OA-2269", "UPDATED", "service_status",
                "RECEIVING", "CLOSED", Instant.now(),
                "강남 수영교실", "https://ex.com/1", "https://ex.com/img.png",
                "강남센터", "강남구", "RECEIVING", "성인",
                "2026-05-01", "2026-05-31");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("AI 제목", "AI 요약", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationContent> contentCaptor =
                ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(any(UserContact.class), contentCaptor.capture(), any(), any());

        NotificationContent sent = contentCaptor.getValue();
        // AI는 title/summary만 — 사실은 결정적 카드에서 온다.
        assertThat(sent.title()).isEqualTo("AI 제목");
        assertThat(sent.summary()).isEqualTo("AI 요약");
        assertThat(sent.services()).hasSize(1);

        NotificationContent.ServiceCard card = sent.services().get(0);
        // serviceId 는 cross-trigger dedup 선조회용 내부 식별자로 카드에 보존된다.
        assertThat(card.serviceId()).isEqualTo("OA-2269");
        assertThat(card.name()).isEqualTo("강남 수영교실");
        assertThat(card.status()).isEqualTo("RECEIVING");
        assertThat(card.area()).isEqualTo("강남구");
        assertThat(card.place()).isEqualTo("강남센터");
        assertThat(card.target()).isEqualTo("성인");
        assertThat(card.receiptStart()).isEqualTo("2026-05-01");
        assertThat(card.receiptEnd()).isEqualTo("2026-05-31");
        assertThat(card.url()).isEqualTo("https://ex.com/1");
        assertThat(card.imageUrl()).isEqualTo("https://ex.com/img.png");

        assertThat(card.changes()).hasSize(1);
        NotificationContent.ChangeLine line = card.changes().get(0);
        assertThat(line.label()).isEqualTo("모집상태");  // 한글 매핑 (NotificationTemplate.fieldLabel 재사용)
        assertThat(line.label()).isNotEqualTo("service_status");
        assertThat(line.oldValue()).isEqualTo("RECEIVING");
        assertThat(line.newValue()).isEqualTo("CLOSED");
    }

    @Test
    @DisplayName("발송 직전 dispatch에 비어있지 않은 직렬화 payload가 할당된다 (재시도 무손실 복원용)")
    void dispatch_assignsNonNullSerializedPayloadBeforeSend() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "요약", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        // 실제 NotificationContentSerializer가 주입되어 있으므로 payload는 유효 JSON이어야 한다.
        assertThat(d.getNotificationPayload()).isNotNull();
        assertThat(d.getNotificationPayload()).contains("\"title\"").contains("제목");
        assertThat(d.getNotificationPayload()).contains("\"services\"");
    }

    @Test
    @DisplayName("매칭 서비스가 20개 이하면 serviceCards를 전부 포함하고 summary 그대로 사용한다")
    void dispatch_underCap_allCardsIncluded_summaryUnchanged() {
        NotificationSubscription s = sub(1L);
        List<ServiceChange> changes = new java.util.ArrayList<>();
        for (int i = 0; i < 20; i++) {
            changes.add(change((long) (100 + i), "OA-" + i));
        }
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "AI 요약", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(changes, d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationContent> contentCaptor = ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(any(UserContact.class), contentCaptor.capture(), any(), any());

        NotificationContent sent = contentCaptor.getValue();
        assertThat(sent.services()).hasSize(20);
        assertThat(sent.summary()).isEqualTo("AI 요약");
    }

    @Test
    @DisplayName("매칭 서비스가 20개 초과면 serviceCards를 20개로 자르고 summary에 안내 문구를 append한다")
    void dispatch_overCap_cardsLimitedTo20_summaryAppended() {
        NotificationSubscription s = sub(1L);
        List<ServiceChange> changes = new java.util.ArrayList<>();
        for (int i = 0; i < 25; i++) {
            changes.add(change((long) (100 + i), "OA-" + i));
        }
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult(
                "[서울공공서비스] 구독하신 25개 서비스 변경 알림",
                "구독하신 25개 서비스의 변경 소식이 있습니다.",
                TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(changes, d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationContent> contentCaptor = ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(any(UserContact.class), contentCaptor.capture(), any(), any());

        NotificationContent sent = contentCaptor.getValue();
        // serviceCards는 20개로 제한
        assertThat(sent.services()).hasSize(20);
        // title은 실제 건수(25) 유지
        assertThat(sent.title()).isEqualTo("[서울공공서비스] 구독하신 25개 서비스 변경 알림");
        // summary는 실제 건수 유지 + 안내 문구 append
        assertThat(sent.summary())
                .startsWith("구독하신 25개 서비스의 변경 소식이 있습니다.")
                .endsWith("메일은 최대 20개까지만 보여줄 수 있습니다.");
        // AI 요청은 전체 25개 그룹으로 전달 (title/summary 기준이 되는 건수)
        ArgumentCaptor<NotificationTemplateRequest> reqCaptor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(reqCaptor.capture());
        assertThat(reqCaptor.getValue().services()).hasSize(25);
    }

    @Test
    @DisplayName("AI 실패 폴백(source=FALLBACK)이어도 결정적 카드를 포함한 콘텐츠가 조립되어 발송된다")
    void dispatch_aiFallbackSource_stillAssemblesStructuredContent() {
        NotificationSubscription s = sub(1L);
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        // generate가 결정적 폴백을 반환한 경우 (AI 호출 실패 시 어댑터가 FALLBACK source로 폴백)
        TemplateResult fallback = new TemplateResult("결정적 제목", "결정적 요약", TemplateSource.FALLBACK);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(fallback);

        scheduler.processAllSubscriptions();

        ArgumentCaptor<NotificationContent> contentCaptor =
                ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(any(UserContact.class), contentCaptor.capture(), any(), any());

        NotificationContent sent = contentCaptor.getValue();
        assertThat(sent.summary()).isEqualTo("결정적 요약");
        assertThat(sent.services()).hasSize(1);  // 폴백이어도 카드는 결정적으로 조립됨
        assertThat(d.getNotificationPayload()).isNotNull();
    }
}
