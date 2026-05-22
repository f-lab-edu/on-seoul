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

import java.time.Instant;
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
    @Mock private SaveDispatchPort saveDispatchPort;
    @Mock private SaveSubscriptionPort saveSubscriptionPort;
    @Mock private SubscriptionFilterParserPort subscriptionFilterParserPort;

    private NotificationTxHelper txHelper;

    @BeforeEach
    void setUp() {
        txHelper = new NotificationTxHelper(
                loadServiceChangePort, saveDispatchPort, saveSubscriptionPort, subscriptionFilterParserPort);
    }

    // ── helpers ──────────────────────────────────────────────────────────

    private NotificationSubscription subscription(Long lastNotifiedAtNullable, String filterJson) {
        Instant last = lastNotifiedAtNullable == null ? null : Instant.ofEpochSecond(0);
        return new NotificationSubscription(1L, 100L, "OA-2269",
                filterJson, Set.of(NotificationChannel.EMAIL), last, Instant.now());
    }

    private NotificationSubscription subscriptionWithLastNotifiedAt(Instant lastNotifiedAt) {
        return new NotificationSubscription(1L, 100L, "OA-2269",
                "{}", Set.of(NotificationChannel.EMAIL), lastNotifiedAt, Instant.now());
    }

    private NotificationBatch batchStartedAt(Instant startedAt) {
        return new NotificationBatch(99L, startedAt, null, BatchStatus.RUNNING, null, null);
    }

    private ServiceChange changeAt(Instant changedAt) {
        return new ServiceChange(10L, "OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", changedAt);
    }

    private NotificationDispatch pendingDispatch() {
        return NotificationDispatch.create(99L, 1L);
    }

    // ── TX A ─────────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX A — 변경이 존재하면 saveIfAbsent로 dispatch INSERT 후 (changes, dispatch) 반환")
    void txA_withChanges_savesDispatchAndReturns() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());

        when(subscriptionFilterParserPort.parse("{}")).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered("OA-2269", SubscriptionFilter.empty(), null))
                .thenReturn(List.of(change));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.of(pendingDispatch()));

        NotificationTxHelper.TxAResult result = txHelper.txA(99L, sub);

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

        when(subscriptionFilterParserPort.parse(any())).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(any(), any(), any())).thenReturn(List.of());

        NotificationTxHelper.TxAResult result = txHelper.txA(99L, sub);

        assertThat(result.changes()).isEmpty();
        assertThat(result.dispatch()).isEmpty();
        verify(saveDispatchPort, never()).saveIfAbsent(any());
    }

    @Test
    @DisplayName("TX A — saveIfAbsent가 empty(중복) 반환 시 dispatch는 empty지만 changes는 그대로 반환")
    void txA_duplicateDispatch_returnsEmptyDispatch() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());

        when(subscriptionFilterParserPort.parse(any())).thenReturn(SubscriptionFilter.empty());
        when(loadServiceChangePort.loadFiltered(any(), any(), any())).thenReturn(List.of(change));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.empty());

        NotificationTxHelper.TxAResult result = txHelper.txA(99L, sub);

        assertThat(result.changes()).hasSize(1);
        assertThat(result.dispatch()).isEmpty();
    }

    @Test
    @DisplayName("TX A — subscriptionFilter 파서를 거쳐 loadFiltered에 전달된다")
    void txA_filterParsedAndPassed() {
        NotificationSubscription sub = subscription(null, "{\"statuses\":[\"RECEIVING\"]}");
        SubscriptionFilter parsed = new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of());

        when(subscriptionFilterParserPort.parse("{\"statuses\":[\"RECEIVING\"]}")).thenReturn(parsed);
        when(loadServiceChangePort.loadFiltered("OA-2269", parsed, null)).thenReturn(List.of());

        txHelper.txA(99L, sub);

        verify(subscriptionFilterParserPort).parse("{\"statuses\":[\"RECEIVING\"]}");
        verify(loadServiceChangePort).loadFiltered("OA-2269", parsed, null);
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
    @DisplayName("TX B 실패 — dispatch FAILED + last_error만 갱신, subscription 미수정")
    void txBFailure_marksFailedAndSkipsSubscriptionUpdate() {
        Instant original = Instant.parse("2026-05-10T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(original);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBFailure(dispatch, "발송 오류");

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getLastError()).isEqualTo("발송 오류");
        // subscription은 절대 저장되지 않음 — last_notified_at 미갱신 정책
        verifyNoInteractions(saveSubscriptionPort);
        assertThat(sub.getLastNotifiedAt()).isEqualTo(original);
        verify(saveDispatchPort).save(eq(dispatch));
    }
}
