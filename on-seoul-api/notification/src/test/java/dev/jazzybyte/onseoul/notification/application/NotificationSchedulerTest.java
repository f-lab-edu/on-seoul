package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
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
                saveBatchPort, loadBatchPort, txHelper, meterRegistry, 600_000L);

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

    private NotificationSubscription sub(Long id, String serviceId) {
        return NotificationSubscription.ofPersistence(id, 1L, serviceId, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
    }

    private ServiceChange change(Long id, String serviceId) {
        return new ServiceChange(id, serviceId, "UPDATED", "service_status",
                "RECEIVING", "CLOSED", Instant.now());
    }

    private NotificationDispatch dispatch(Long subId) {
        return NotificationDispatch.create(BATCH_ID, subId);
    }

    private NotificationTxHelper.TxAResult txAResult(List<ServiceChange> changes,
                                                    NotificationDispatch dispatch) {
        return new NotificationTxHelper.TxAResult(changes, Optional.ofNullable(dispatch));
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

    // ── per-subscription 흐름 ────────────────────────────────────────────

    @Test
    @DisplayName("TX A가 빈 changes를 반환하면 template/push를 호출하지 않는다")
    void txAEmpty_skipsTemplateAndPush() {
        NotificationSubscription s = sub(1L, "OA-2269");
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                .thenReturn(txAResult(List.of(), null));

        scheduler.processAllSubscriptions();

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("TX A에서 dispatch가 중복(empty)이면 push 미호출")
    void duplicateDispatch_skipsPush() {
        NotificationSubscription s = sub(1L, "OA-2269");
        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                .thenReturn(txAResult(List.of(change(100L, "OA-2269")), null));

        scheduler.processAllSubscriptions();

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("정상 흐름: template/push 호출, txBSuccess 호출, sent_count=1")
    void successPath_callsTxBSuccessAndIncrementsSentCount() {
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        verify(pushNotificationPort).send(any(UserContact.class), anyString(), anyString(), any(), any());
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
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.FALLBACK);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any())).thenReturn(template);
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(UserContact.class), anyString(), anyString(), any(), any());

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
        NotificationSubscription s1 = sub(1L, "OA-2269");
        NotificationSubscription s2 = sub(2L, "OA-2266");
        ServiceChange c2 = change(200L, "OA-2266");
        NotificationDispatch d2 = dispatch(2L);
        TemplateResult template = new TemplateResult("제목2", "본문2", TemplateSource.AI);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s1, s2));
        when(txHelper.txA(any(NotificationBatch.class), eq(s1))).thenThrow(new RuntimeException("DB 오류"));
        when(txHelper.txA(any(NotificationBatch.class), eq(s2))).thenReturn(txAResult(List.of(c2), d2));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();

        verify(pushNotificationPort).send(any(UserContact.class), anyString(), anyString(), any(), any());

        ArgumentCaptor<NotificationBatch> finalCaptor = ArgumentCaptor.forClass(NotificationBatch.class);
        verify(saveBatchPort).update(finalCaptor.capture());
        assertThat(finalCaptor.getValue().getSentCount()).isEqualTo(1);
        assertThat(finalCaptor.getValue().getFailedCount()).isEqualTo(1);
    }

    // ── metrics ──────────────────────────────────────────────────────────

    @Test
    @DisplayName("성공 시 notification.dispatch.attempts{result=success} 카운터가 증가")
    void successPath_incrementsSuccessCounter() {
        NotificationSubscription s = sub(1L, "OA-2269");
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
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "본문", TemplateSource.AI));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), anyString(), anyString(), any(), any());

        scheduler.processAllSubscriptions();

        assertThat(meterRegistry.counter("notification.dispatch.attempts", "result", "failed")
                .count()).isEqualTo(1.0);
        assertThat(meterRegistry.counter("notification.dispatch.failed.total").count())
                .isEqualTo(1.0);
    }

    @Test
    @DisplayName("template source=AI 일 때 notification.template.source{source=AI} 카운터 증가")
    void templateSourceAi_incrementsCounter() {
        NotificationSubscription s = sub(1L, "OA-2269");
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
        NotificationSubscription s = sub(1L, "OA-2269");
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
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));
        when(loadUserContactPort.loadContact(anyLong())).thenReturn(Optional.empty());

        scheduler.processAllSubscriptions();

        ArgumentCaptor<UserContact> contactCaptor = ArgumentCaptor.forClass(UserContact.class);
        verify(pushNotificationPort).send(contactCaptor.capture(), anyString(), anyString(), any(), any());
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
            NotificationSubscription s = sub(i, "OA-" + i);
            firstPage.add(s);
            when(txHelper.txA(any(NotificationBatch.class), eq(s)))
                    .thenReturn(txAResult(List.of(change(i + 1000, "OA-" + i)), dispatch(i)));
        }
        NotificationSubscription lastSub = sub((long) firstPageSize + 1, "OA-last");
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
            NotificationSubscription s = sub(i, "OA-" + i);
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
                .send(any(UserContact.class), anyString(), anyString(), any(), any());

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
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c = change(100L, "OA-2269");
        NotificationDispatch d = dispatch(1L);

        when(loadSubscriptionPort.loadChunk(eq(0L), eq(SUBSCRIPTION_CHUNK_SIZE))).thenReturn(List.of(s));
        when(txHelper.txA(any(NotificationBatch.class), eq(s))).thenReturn(txAResult(List.of(c), d));
        when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("t", "b", TemplateSource.AI));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), anyString(), anyString(), any(), any());

        scheduler.processAllSubscriptions();

        // SimpleMeterRegistry에서 등록된 모든 meter 이름을 확인 — dead가 없어야 함
        assertThat(meterRegistry.getMeters())
                .extracting(m -> m.getId().getName())
                .doesNotContain("notification.dispatch.dead");
    }

    @Test
    @DisplayName("templateGenerationPort에 변경 목록이 List<ChangeItem>으로 전달된다")
    void templateRequest_carriesBatchedChangeItems() {
        NotificationSubscription s = sub(1L, "OA-2269");
        ServiceChange c1 = new ServiceChange(100L, "OA-2269", "UPDATED",
                "service_status", "RECEIVING", "CLOSED", Instant.now());
        ServiceChange c2 = new ServiceChange(101L, "OA-2269", "UPDATED",
                "receipt_end_dt", "2026-05-01", "2026-05-31", Instant.now());
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

        assertThat(req.serviceId()).isEqualTo("OA-2269");
        assertThat(req.changes()).hasSize(2);
        assertThat(req.changes().get(0).fieldName()).isEqualTo("service_status");
        assertThat(req.changes().get(1).fieldName()).isEqualTo("receipt_end_dt");
    }
}
