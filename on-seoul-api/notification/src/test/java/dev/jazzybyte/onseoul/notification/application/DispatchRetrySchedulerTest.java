package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
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
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DispatchRetrySchedulerTest {

    @Mock private LoadDispatchPort loadDispatchPort;
    @Mock private LoadSubscriptionPort loadSubscriptionPort;
    @Mock private LoadUserContactPort loadUserContactPort;
    @Mock private PushNotificationPort pushNotificationPort;
    @Mock private NotificationTxHelper txHelper;

    private DispatchRetryScheduler scheduler;

    private static final Long SUB_ID = 10L;
    private static final Long USER_ID = 1L;
    private static final UserContact CONTACT = new UserContact(USER_ID, "user@example.com", null);

    @BeforeEach
    void setUp() {
        scheduler = new DispatchRetryScheduler(
                loadDispatchPort, loadSubscriptionPort, loadUserContactPort,
                pushNotificationPort, txHelper);
    }

    private NotificationDispatch failedDispatch(Long id, int attemptCount) {
        NotificationDispatch d = new NotificationDispatch(
                id, 1L, SUB_ID, DispatchStatus.FAILED,
                null, "재시도 제목", "재시도 본문", TemplateSource.AI,
                "이전 오류", attemptCount,
                Instant.now(), Instant.now());
        return d;
    }

    private NotificationSubscription subscription() {
        return NotificationSubscription.ofPersistence(
                SUB_ID, USER_ID, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
    }

    // ── 재시도 대상 없음 ─────────────────────────────────────────────────

    @Test
    @DisplayName("FAILED dispatch 없음 → send 호출 없음")
    void noRetryable_doesNotSend() {
        lenient().when(loadDispatchPort.findRetryable()).thenReturn(List.of());

        scheduler.retryFailedDispatches();

        verifyNoInteractions(pushNotificationPort, txHelper);
    }

    // ── 재시도 성공 ──────────────────────────────────────────────────────

    @Test
    @DisplayName("재시도 성공 → txBRetrySuccess 호출, SUCCESS 상태 + last_notified_at 전진")
    void retrySuccess_callsTxBRetrySuccessAndAdvancesLastNotifiedAt() {
        NotificationDispatch dispatch = failedDispatch(100L, 2);
        NotificationSubscription sub = subscription();

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(sub));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));

        scheduler.retryFailedDispatches();

        verify(pushNotificationPort).send(
                eq(CONTACT), eq("재시도 제목"), eq("재시도 본문"), eq(100L), any());

        ArgumentCaptor<NotificationDispatch> dispatchCaptor =
                ArgumentCaptor.forClass(NotificationDispatch.class);
        ArgumentCaptor<NotificationSubscription> subCaptor =
                ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(txHelper).txBRetrySuccess(dispatchCaptor.capture(), subCaptor.capture(), any(Instant.class));
        assertThat(dispatchCaptor.getValue().getId()).isEqualTo(100L);
        assertThat(subCaptor.getValue().getId()).isEqualTo(SUB_ID);
    }

    // ── 재시도 실패 (attempt_count < 4) ────────────────────────────────

    @Test
    @DisplayName("재시도 실패 (attemptCount=3) → FAILED 유지 + attemptCount=4 + txBRetryFailure")
    void retryFails_withAttemptCount3_remainsFailedAndIncrements() {
        NotificationDispatch dispatch = failedDispatch(100L, 3);
        NotificationSubscription sub = subscription();

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(sub));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), anyString(), anyString(), any(), any());

        scheduler.retryFailedDispatches();

        assertThat(dispatch.getAttemptCount()).isEqualTo(4);
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);

        verify(txHelper).txBRetryFailure(eq(dispatch));
        verify(txHelper, never()).txBRetrySuccess(any(), any(), any());
    }

    // ── 재시도 실패 (attempt_count → 5 = DEAD) ─────────────────────────

    @Test
    @DisplayName("재시도 실패 (attemptCount=4) → DEAD 전환 + attemptCount=5 + txBRetryFailure")
    void retryFails_withAttemptCount4_transitionsToDead() {
        NotificationDispatch dispatch = failedDispatch(100L, 4);
        NotificationSubscription sub = subscription();

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(sub));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));
        doThrow(new RuntimeException("최종 실패")).when(pushNotificationPort)
                .send(any(), anyString(), anyString(), any(), any());

        scheduler.retryFailedDispatches();

        assertThat(dispatch.getAttemptCount()).isEqualTo(5);
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getLastError()).isEqualTo("최종 실패");

        verify(txHelper).txBRetryFailure(eq(dispatch));
        verify(txHelper, never()).txBRetrySuccess(any(), any(), any());
    }

    // ── 구독 없음 (삭제된 구독) ──────────────────────────────────────────

    @Test
    @DisplayName("구독 없음(삭제된 구독) → 해당 dispatch 스킵 (send/txHelper 미호출)")
    void subscriptionNotFound_skipsDispatch() {
        NotificationDispatch dispatch = failedDispatch(100L, 1);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.empty());

        scheduler.retryFailedDispatches();

        verifyNoInteractions(pushNotificationPort, txHelper);
    }

    // ── 연락처 없음 → fallback ────────────────────────────────────────────

    @Test
    @DisplayName("연락처 미등록 → userId만으로 발송 시도 (fallback UserContact)")
    void contactNotFound_usesUserIdFallback() {
        NotificationDispatch dispatch = failedDispatch(100L, 0);
        NotificationSubscription sub = subscription();

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(sub));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.empty());

        scheduler.retryFailedDispatches();

        ArgumentCaptor<UserContact> contactCaptor = ArgumentCaptor.forClass(UserContact.class);
        verify(pushNotificationPort).send(contactCaptor.capture(), anyString(), anyString(), any(), any());
        assertThat(contactCaptor.getValue().userId()).isEqualTo(USER_ID);
        assertThat(contactCaptor.getValue().email()).isNull();
    }
}
