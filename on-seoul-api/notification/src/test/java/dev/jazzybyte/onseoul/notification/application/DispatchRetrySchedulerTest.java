package dev.jazzybyte.onseoul.notification.application;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.adapter.out.persistence.NotificationContentSerializer;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
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

    private final NotificationContentSerializerPort contentSerializer =
            new NotificationContentSerializer(new ObjectMapper());

    private DispatchRetryScheduler scheduler;

    private static final Long SUB_ID = 10L;
    private static final Long USER_ID = 1L;
    private static final UserContact CONTACT = new UserContact(USER_ID, "user@example.com", null);

    private static final int MAX_AGE_HOURS = 12;

    @BeforeEach
    void setUp() {
        scheduler = new DispatchRetryScheduler(
                loadDispatchPort, loadSubscriptionPort, loadUserContactPort,
                pushNotificationPort, txHelper, contentSerializer, MAX_AGE_HOURS);
    }

    private NotificationDispatch failedDispatch(Long id, int attemptCount) {
        return failedDispatch(id, attemptCount, null);
    }

    private NotificationDispatch failedDispatch(Long id, int attemptCount, String payload) {
        return failedDispatch(id, attemptCount, payload, Instant.now());
    }

    private NotificationDispatch failedDispatch(Long id, int attemptCount, String payload, Instant createdAt) {
        return new NotificationDispatch(
                id, 1L, SUB_ID,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.FAILED,
                null, "재시도 제목", "재시도 본문", TemplateSource.AI,
                "이전 오류", attemptCount, payload,
                createdAt, createdAt);
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
                eq(CONTACT), any(NotificationContent.class), eq(100L), any());

        ArgumentCaptor<NotificationDispatch> dispatchCaptor =
                ArgumentCaptor.forClass(NotificationDispatch.class);
        ArgumentCaptor<NotificationSubscription> subCaptor =
                ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(txHelper).txBRetrySuccess(dispatchCaptor.capture(), subCaptor.capture(), any(Instant.class));
        assertThat(dispatchCaptor.getValue().getId()).isEqualTo(100L);
        assertThat(subCaptor.getValue().getId()).isEqualTo(SUB_ID);
    }

    // ── payload 존재 → 구조화 콘텐츠 복원 후 무손실 재발송 ──────────────

    @Test
    @DisplayName("notification_payload 존재 → 직렬화된 NotificationContent를 복원하여 재발송")
    void retrySuccess_withPayload_resendsDeserializedContent() {
        NotificationContent original = new NotificationContent(
                "구독하신 2개 서비스 변경 알림", "구독하신 2개 서비스에 변경이 감지되었습니다.",
                List.of(new NotificationContent.ServiceCard(
                        "OA-2269", "강남 수영교실", "예약마감", "강남구", "강남센터", "성인",
                        "2026-05-01", "2026-05-31",
                        "https://ex.com/1", "https://ex.com/img.png",
                        List.of(new NotificationContent.ChangeLine("모집상태", "접수중", "예약마감")))));
        String payload = contentSerializer.serialize(original);

        NotificationDispatch dispatch = failedDispatch(100L, 1, payload);
        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(subscription()));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));

        scheduler.retryFailedDispatches();

        ArgumentCaptor<NotificationContent> contentCaptor =
                ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(eq(CONTACT), contentCaptor.capture(), eq(100L), any());

        NotificationContent sent = contentCaptor.getValue();
        assertThat(sent.title()).isEqualTo("구독하신 2개 서비스 변경 알림");
        assertThat(sent.services()).hasSize(1);
        assertThat(sent.services().get(0).name()).isEqualTo("강남 수영교실");
        assertThat(sent.services().get(0).changes().get(0).label()).isEqualTo("모집상태");
    }

    // ── payload null → generated_title/body 평문 폴백 재발송 ─────────────

    @Test
    @DisplayName("notification_payload null(이전 row) → generated_title/body 평문 폴백으로 재발송")
    void retrySuccess_nullPayload_usesPlainFallback() {
        NotificationDispatch dispatch = failedDispatch(100L, 1, null);
        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(subscription()));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));

        scheduler.retryFailedDispatches();

        ArgumentCaptor<NotificationContent> contentCaptor =
                ArgumentCaptor.forClass(NotificationContent.class);
        verify(pushNotificationPort).send(eq(CONTACT), contentCaptor.capture(), eq(100L), any());

        NotificationContent sent = contentCaptor.getValue();
        assertThat(sent.title()).isEqualTo("재시도 제목");
        assertThat(sent.summary()).isEqualTo("재시도 본문");
        assertThat(sent.services()).isEmpty();
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
                .send(any(), any(NotificationContent.class), any(), any());

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
                .send(any(), any(NotificationContent.class), any(), any());

        scheduler.retryFailedDispatches();

        assertThat(dispatch.getAttemptCount()).isEqualTo(5);
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getLastError()).isEqualTo("최종 실패");

        verify(txHelper).txBRetryFailure(eq(dispatch));
        verify(txHelper, never()).txBRetrySuccess(any(), any(), any());
    }

    // ── staleness 가드 (createdAt 기준 max-age 초과) ─────────────────────

    @Test
    @DisplayName("createdAt 12h 초과 FAILED → send 미호출, EXPIRED 전환 + txBRetryExpired (attempt_count 불변)")
    void staleDispatch_expiresWithoutSending() {
        Instant created = Instant.now().minus(java.time.Duration.ofHours(13));
        NotificationDispatch dispatch = failedDispatch(100L, 2, null, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));

        scheduler.retryFailedDispatches();

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.EXPIRED);
        assertThat(dispatch.getAttemptCount()).isEqualTo(2);

        verify(txHelper).txBRetryExpired(eq(dispatch));
        verifyNoInteractions(pushNotificationPort, loadSubscriptionPort, loadUserContactPort);
        verify(txHelper, never()).txBRetrySuccess(any(), any(), any());
        verify(txHelper, never()).txBRetryFailure(any());
    }

    @Test
    @DisplayName("createdAt 12h 이내 FAILED → 정상 재시도 (send 호출, EXPIRED 미전환)")
    void freshDispatch_retriesNormally() {
        Instant created = Instant.now().minus(java.time.Duration.ofHours(11));
        NotificationDispatch dispatch = failedDispatch(100L, 1, null, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(subscription()));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));

        scheduler.retryFailedDispatches();

        verify(pushNotificationPort).send(eq(CONTACT), any(NotificationContent.class), eq(100L), any());
        verify(txHelper, never()).txBRetryExpired(any());
    }

    @Test
    @DisplayName("경계: createdAt 정확히 임계값(12h)이면 EXPIRED 아님 — 정상 재시도")
    void boundary_exactlyAtThreshold_retries() {
        // retryStartedAt - createdAt == maxAge → isBefore false → 재시도
        Instant created = Instant.now().minus(java.time.Duration.ofHours(MAX_AGE_HOURS)).plusSeconds(2);
        NotificationDispatch dispatch = failedDispatch(100L, 1, null, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        when(loadSubscriptionPort.loadById(SUB_ID)).thenReturn(Optional.of(subscription()));
        when(loadUserContactPort.loadContact(USER_ID)).thenReturn(Optional.of(CONTACT));

        scheduler.retryFailedDispatches();

        verify(pushNotificationPort).send(eq(CONTACT), any(NotificationContent.class), eq(100L), any());
        verify(txHelper, never()).txBRetryExpired(any());
    }

    @Test
    @DisplayName("max-age-hours 주입값 반영 — 6h 설정 시 7h 경과 FAILED는 EXPIRED")
    void injectedMaxAge_isHonored() {
        DispatchRetryScheduler shortScheduler = new DispatchRetryScheduler(
                loadDispatchPort, loadSubscriptionPort, loadUserContactPort,
                pushNotificationPort, txHelper, contentSerializer, 6);

        Instant created = Instant.now().minus(java.time.Duration.ofHours(7));
        NotificationDispatch dispatch = failedDispatch(100L, 1, null, created);
        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));

        shortScheduler.retryFailedDispatches();

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.EXPIRED);
        verify(txHelper).txBRetryExpired(eq(dispatch));
        verifyNoInteractions(pushNotificationPort);
    }

    @Test
    @DisplayName("PII: markExpired reason에 제목·본문 평문이 포함되지 않는다")
    void staleDispatch_expiredReason_containsNoPii() {
        Instant created = Instant.now().minus(java.time.Duration.ofHours(13));
        // 제목/본문에 식별 가능한 PII 성격 문자열을 넣어 reason 누출 여부를 검증한다.
        NotificationDispatch dispatch = new NotificationDispatch(
                100L, 1L, SUB_ID,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.FAILED, null,
                "민감-제목-홍길동", "민감-본문-강남구수영교실", TemplateSource.AI,
                "이전 오류", 2, null, created, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));

        scheduler.retryFailedDispatches();

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.EXPIRED);
        assertThat(dispatch.getLastError())
                .doesNotContain("민감-제목-홍길동")
                .doesNotContain("민감-본문-강남구수영교실")
                .contains("max-age")
                .contains(String.valueOf(MAX_AGE_HOURS));
        // 제목/본문 자체는 재시도 복원용으로 도메인에 보존되어야 한다(폐기지 삭제 아님).
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("민감-제목-홍길동");
        verifyNoInteractions(pushNotificationPort);
    }

    @Test
    @DisplayName("txBRetryExpired TX 실패해도 루프가 죽지 않고 send도 호출하지 않는다")
    void staleDispatch_txExpiredThrows_doesNotSendAndSwallows() {
        Instant created = Instant.now().minus(java.time.Duration.ofHours(13));
        NotificationDispatch dispatch = failedDispatch(100L, 2, null, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));
        doThrow(new RuntimeException("DB down")).when(txHelper).txBRetryExpired(any());

        // 예외가 전파되지 않아야 한다(스케줄러는 단건 실패를 삼킨다).
        scheduler.retryFailedDispatches();

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.EXPIRED);
        verify(txHelper).txBRetryExpired(eq(dispatch));
        verifyNoInteractions(pushNotificationPort, loadSubscriptionPort);
    }

    @Test
    @DisplayName("staleness 가드 우선: 재시도 소진(attemptCount=4) + stale 이면 send 없이 EXPIRED (DEAD 아님)")
    void staleAndRetryExhausted_takesExpiredPathNotDead() {
        Instant created = Instant.now().minus(java.time.Duration.ofHours(13));
        NotificationDispatch dispatch = failedDispatch(100L, 4, null, created);

        when(loadDispatchPort.findRetryable()).thenReturn(List.of(dispatch));

        scheduler.retryFailedDispatches();

        // staleness 가드가 send 이전에 동작하므로 DEAD 경로(send 실패 후 markDead)에 닿지 않는다.
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.EXPIRED);
        assertThat(dispatch.getAttemptCount()).isEqualTo(4);
        verify(txHelper).txBRetryExpired(eq(dispatch));
        verify(txHelper, never()).txBRetryFailure(any());
        verifyNoInteractions(pushNotificationPort);
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
        verify(pushNotificationPort).send(
                contactCaptor.capture(), any(NotificationContent.class), any(), any());
        assertThat(contactCaptor.getValue().userId()).isEqualTo(USER_ID);
        assertThat(contactCaptor.getValue().email()).isNull();
    }
}
