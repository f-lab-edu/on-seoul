package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class NotificationTxHelperTest {

    @Mock private LoadServiceChangePort loadServiceChangePort;
    @Mock private LoadDispatchPort loadDispatchPort;
    @Mock private SaveDispatchPort saveDispatchPort;
    @Mock private SaveSubscriptionPort saveSubscriptionPort;
    @Mock private SubscriptionFilterParserPort subscriptionFilterParserPort;

    private NotificationTxHelper txHelper;

    /** 종료 서비스 제외 D-day 기준을 결정적으로 만들기 위한 고정 UTC Clock. */
    private static final Instant FIXED_NOW = Instant.parse("2026-05-22T09:00:00Z");
    private static final LocalDate TODAY = LocalDate.ofInstant(FIXED_NOW, ZoneOffset.UTC);

    @BeforeEach
    void setUp() {
        txHelper = new NotificationTxHelper(
                loadServiceChangePort, loadDispatchPort, saveDispatchPort,
                saveSubscriptionPort, subscriptionFilterParserPort,
                Clock.fixed(FIXED_NOW, ZoneOffset.UTC));
    }

    // ── helpers ──────────────────────────────────────────────────────────

    private static final Instant BATCH_STARTED = Instant.parse("2026-05-22T09:00:00Z");
    private static final NotificationBatch TEST_BATCH =
            new NotificationBatch(99L, BATCH_STARTED, null, BatchStatus.RUNNING, null, null);

    private NotificationSubscription subscription(Long lastNotifiedAtNullable, String filterJson) {
        Instant last = lastNotifiedAtNullable == null ? null : Instant.ofEpochSecond(0);
        return NotificationSubscription.ofPersistence(1L, 100L,
                filterJson, Set.of(NotificationChannel.EMAIL), last, Instant.now());
    }

    private NotificationSubscription subscriptionWithLastNotifiedAt(Instant lastNotifiedAt) {
        return NotificationSubscription.ofPersistence(1L, 100L,
                "{}", Set.of(NotificationChannel.EMAIL), lastNotifiedAt, Instant.now());
    }

    private NotificationBatch batchStartedAt(Instant startedAt) {
        return new NotificationBatch(99L, startedAt, null, BatchStatus.RUNNING, null, null);
    }

    private ServiceChange changeAt(Instant changedAt) {
        return new ServiceChange(10L, "OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", changedAt,
                "수영교실", null, null, null, null, "RECEIVING", null, null, null);
    }

    private NotificationDispatch pendingDispatch() {
        return NotificationDispatch.create(99L, 1L);
    }

    // ── TX A ─────────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX A — DEAD dispatch가 존재하면 변경 조회 없이 empty 반환 (영구 실패 가드)")
    void txA_deadDispatchExists_skipsAllAndReturnsEmpty() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(true);

        NotificationTxHelper.TxAResult result = txHelper.txA(TEST_BATCH, sub);

        assertThat(result.changes()).isEmpty();
        assertThat(result.dispatch()).isEmpty();
        // DEAD 가드 발동 시 변경 조회, saveIfAbsent, filter 파싱을 모두 호출하지 않는다
        verifyNoInteractions(loadServiceChangePort, saveDispatchPort, subscriptionFilterParserPort);
    }

    @Test
    @DisplayName("TX A — DEAD 가드 발동 시 subscriptionFilterParserPort도 호출되지 않는다 (filter 파싱 생략 확인)")
    void txA_deadDispatchExists_doesNotParseFilter() {
        // filter JSON이 복잡한 경우에도 파싱 자체가 발생하지 않아야 한다
        NotificationSubscription sub = subscription(null, "{\"statuses\":[\"RECEIVING\"],\"areaNames\":[\"강남구\"]}");
        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(true);

        txHelper.txA(TEST_BATCH, sub);

        verifyNoInteractions(subscriptionFilterParserPort);
    }

    @Test
    @DisplayName("TX A — DEAD dispatch가 없으면 정상 흐름으로 진행한다")
    void txA_noDeadDispatch_proceedsNormally() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse("{}")).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(SubscriptionFilter.empty(), null, BATCH_STARTED, TODAY))
                .thenReturn(List.of(change));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.of(pendingDispatch()));

        NotificationTxHelper.TxAResult result = txHelper.txA(TEST_BATCH, sub);

        assertThat(result.changes()).hasSize(1);
        assertThat(result.dispatch()).isPresent();
    }

    @Test
    @DisplayName("TX A — 변경이 존재하면 saveIfAbsent로 dispatch INSERT 후 (changes, dispatch) 반환")
    void txA_withChanges_savesDispatchAndReturns() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse("{}")).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(SubscriptionFilter.empty(), null, BATCH_STARTED, TODAY))
                .thenReturn(List.of(change));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.of(pendingDispatch()));

        NotificationTxHelper.TxAResult result = txHelper.txA(TEST_BATCH, sub);

        assertThat(result.changes()).hasSize(1);
        assertThat(result.dispatch()).isPresent();

        ArgumentCaptor<NotificationDispatch> captor = ArgumentCaptor.forClass(NotificationDispatch.class);
        verify(saveDispatchPort).saveIfAbsent(captor.capture());
        assertThat(captor.getValue().getBatchId()).isEqualTo(99L);
        assertThat(captor.getValue().getSubscriptionId()).isEqualTo(1L);
    }

    @Test
    @DisplayName("TX A — 변경이 없으면 saveIfAbsent 미호출, dispatch Optional.empty")
    void txA_noChanges_skipsDispatchInsert() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse(any())).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(any(), any(), any(), any())).thenReturn(List.of());

        NotificationTxHelper.TxAResult result = txHelper.txA(TEST_BATCH, sub);

        assertThat(result.changes()).isEmpty();
        assertThat(result.dispatch()).isEmpty();
        verify(saveDispatchPort, never()).saveIfAbsent(any());
    }

    @Test
    @DisplayName("TX A — saveIfAbsent가 empty(중복) 반환 시 dispatch는 empty지만 changes는 그대로 반환")
    void txA_duplicateDispatch_returnsEmptyDispatch() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse(any())).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(any(), any(), any(), any())).thenReturn(List.of(change));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.empty());

        NotificationTxHelper.TxAResult result = txHelper.txA(TEST_BATCH, sub);

        assertThat(result.changes()).hasSize(1);
        assertThat(result.dispatch()).isEmpty();
    }

    @Test
    @DisplayName("TX A — subscriptionFilter 파서를 거쳐 loadFiltered에 전달된다")
    void txA_filterParsedAndPassed() {
        NotificationSubscription sub = subscription(null, "{\"statuses\":[\"RECEIVING\"]}");
        SubscriptionFilter parsed = new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of());

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse("{\"statuses\":[\"RECEIVING\"]}")).thenReturn(parsed);
        when(loadServiceChangePort.loadFiltered(parsed, null, BATCH_STARTED, TODAY))
                .thenReturn(List.of());

        txHelper.txA(TEST_BATCH, sub);

        verify(subscriptionFilterParserPort).parse("{\"statuses\":[\"RECEIVING\"]}");
        verify(loadServiceChangePort).loadFiltered(parsed, null, BATCH_STARTED, TODAY);
    }

    @Test
    @DisplayName("TX A — loadFiltered에 batch.startedAt이 상한(changedAtBefore)으로 전달된다")
    void txA_passesStartedAtAsChangedAtBefore() {
        Instant customStarted = Instant.parse("2026-06-01T10:00:00Z");
        NotificationBatch customBatch = new NotificationBatch(77L, customStarted, null, BatchStatus.RUNNING, null, null);
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);

        when(loadDispatchPort.existsDeadDispatchBySubscriptionId(1L)).thenReturn(false);
        when(subscriptionFilterParserPort.parse(any())).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(any(), any(), any(), any())).thenReturn(List.of());

        txHelper.txA(customBatch, sub);

        verify(loadServiceChangePort).loadFiltered(any(), any(), eq(customStarted), eq(TODAY));
    }

    // ── TX B 성공 ────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX B 성공 — last_notified_at은 batch.startedAt으로 전진한다 (change.changedAt 아님)")
    void txBSuccess_advancesLastNotifiedAtToBatchStartedAt() {
        Instant batchStarted = Instant.parse("2026-05-15T09:00:00Z");
        // change는 batchStarted보다 빠른 시각이라도 batch.startedAt이 커서가 된다.
        Instant changedAt = Instant.parse("2026-05-15T08:30:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        NotificationBatch batch = batchStartedAt(batchStarted);
        NotificationDispatch dispatch = pendingDispatch();

        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, batch, "제목", "본문", TemplateSource.AI);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(batchStarted);
        // 부수적으로 changedAt이 사용되지 않음을 확인
        assertThat(sub.getLastNotifiedAt()).isNotEqualTo(changedAt);
    }

    @Test
    @DisplayName("TX B 성공 — dispatch.markSuccess가 적용되고 save가 호출된다")
    void txBSuccess_marksDispatchSuccessAndSaves() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        NotificationBatch batch = batchStartedAt(Instant.now());
        NotificationDispatch dispatch = pendingDispatch();

        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, batch, "제목", "본문", TemplateSource.FALLBACK);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("제목");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.FALLBACK);
        verify(saveDispatchPort).save(dispatch);
        verify(saveSubscriptionPort).save(sub);
    }

    // ── TX B 실패 ────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX B 실패 — dispatch FAILED + title/body/source/last_error 갱신, subscription 미수정")
    void txBFailure_marksFailedAndSkipsSubscriptionUpdate() {
        Instant original = Instant.parse("2026-05-10T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(original);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBFailure(dispatch, "재시도 제목", "재시도 본문", TemplateSource.AI, "발송 오류");

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getLastError()).isEqualTo("발송 오류");
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("재시도 제목");
        assertThat(dispatch.getGeneratedBody()).isEqualTo("재시도 본문");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.AI);
        // subscription은 절대 저장되지 않음 — last_notified_at 미갱신 정책
        verifyNoInteractions(saveSubscriptionPort);
        assertThat(sub.getLastNotifiedAt()).isEqualTo(original);
        verify(saveDispatchPort).save(eq(dispatch));
    }

    // ── Retry TX 성공 ────────────────────────────────────────────────────

    @Test
    @DisplayName("txBRetrySuccess — dispatch SUCCESS 전환 + subscription.last_notified_at = now() 전진")
    void txBRetrySuccess_marksSuccessAndAdvancesLastNotifiedAtToNow() {
        Instant before = Instant.now().minusSeconds(60);
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(before);

        // FAILED dispatch (id=99, generatedTitle/Body/Source 이미 저장된 상태)
        NotificationDispatch dispatch = new NotificationDispatch(
                99L, 1L, sub.getId(),
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.FAILED,
                null, "재시도 제목", "재시도 본문", TemplateSource.AI,
                "이전 오류", 2,
                null, Instant.now(), Instant.now());

        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        Instant retryStartedAt = Instant.now();
        txHelper.txBRetrySuccess(dispatch, sub, retryStartedAt);

        // dispatch는 SUCCESS로 전환되고 generatedTitle/Body 유지
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("재시도 제목");
        assertThat(dispatch.getGeneratedBody()).isEqualTo("재시도 본문");
        assertThat(dispatch.getSentAt()).isNotNull();

        // last_notified_at은 retryStartedAt과 동일해야 함 (TX 내부 now() 아님)
        assertThat(sub.getLastNotifiedAt()).isEqualTo(retryStartedAt);

        verify(saveDispatchPort).save(dispatch);
        verify(saveSubscriptionPort).save(sub);
    }

    // ── Retry TX 실패 ────────────────────────────────────────────────────

    @Test
    @DisplayName("txBRetryFailure — 도메인 상태(FAILED/DEAD)를 그대로 저장하고 subscription은 건드리지 않음")
    void txBRetryFailure_savesDispatchAndSkipsSubscriptionUpdate() {
        Instant original = Instant.parse("2026-05-10T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(original);

        NotificationDispatch dispatch = new NotificationDispatch(
                99L, 1L, sub.getId(),
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.FAILED,
                null, "제목", "본문", TemplateSource.AI,
                "오류", 3,
                null, Instant.now(), Instant.now());

        // 재시도 실패 — 도메인 메서드 먼저 호출한 후 txBRetryFailure
        dispatch.incrementAttemptCount(); // attemptCount=4
        dispatch.markFailed("재시도 오류", dispatch.getGeneratedTitle(), dispatch.getGeneratedBody(),
                dispatch.getTemplateSource());

        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBRetryFailure(dispatch);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getAttemptCount()).isEqualTo(4);
        verify(saveDispatchPort).save(dispatch);
        verifyNoInteractions(saveSubscriptionPort);
        assertThat(sub.getLastNotifiedAt()).isEqualTo(original);
    }

    @Test
    @DisplayName("txBRetryFailure — DEAD 상태 dispatch도 저장 가능")
    void txBRetryFailure_withDeadDispatch_savesDeadStatus() {
        NotificationDispatch dispatch = new NotificationDispatch(
                99L, 1L, 1L,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.FAILED,
                null, "제목", "본문", TemplateSource.AI,
                "오류", 4,
                null, Instant.now(), Instant.now());

        dispatch.incrementAttemptCount(); // attemptCount=5
        dispatch.markDead("최종 실패");

        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBRetryFailure(dispatch);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getAttemptCount()).isEqualTo(5);
        verify(saveDispatchPort).save(dispatch);
        verifyNoInteractions(saveSubscriptionPort);
    }
}
