package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * NotificationTxHelper 단위 테스트.
 * TX A / TX B(성공/실패) 경계 조건을 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class NotificationTxHelperTest {

    @Mock
    private LoadServiceChangePort loadServiceChangePort;

    @Mock
    private SaveDispatchPort saveDispatchPort;

    @Mock
    private SaveSubscriptionPort saveSubscriptionPort;

    private NotificationTxHelper txHelper;

    @BeforeEach
    void setUp() {
        txHelper = new NotificationTxHelper(loadServiceChangePort, saveDispatchPort, saveSubscriptionPort);
    }

    // ── 헬퍼 ──────────────────────────────────────────────────────────────

    private NotificationSubscription subscriptionWithLastNotifiedAt(Instant lastNotifiedAt) {
        return new NotificationSubscription(1L, 100L, "OA-2269", "{}",
                Set.of(NotificationChannel.EMAIL), lastNotifiedAt, Instant.now());
    }

    private ServiceChange changeAt(Instant changedAt) {
        return new ServiceChange(10L, "OA-2269", "CHANGED", "status", "OPEN", "CLOSED", changedAt);
    }

    private NotificationDispatch pendingDispatch() {
        return NotificationDispatch.create(1L, 10L);
    }

    // ── TX A ──────────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX A — lastNotifiedAt=null 이면 전체 이력이 반환되고 각 change에 대해 saveIfAbsent 호출")
    void txA_nullLastNotifiedAt_callsLoadSinceWithNull() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange c1 = changeAt(Instant.now().minus(2, ChronoUnit.HOURS));
        ServiceChange c2 = changeAt(Instant.now().minus(1, ChronoUnit.HOURS));
        when(loadServiceChangePort.loadSince("OA-2269", null)).thenReturn(List.of(c1, c2));
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.of(pendingDispatch()));

        List<ServiceChange> result = txHelper.txA(sub, 5);

        assertThat(result).hasSize(2);
        verify(loadServiceChangePort).loadSince("OA-2269", null);
        verify(saveDispatchPort, times(2)).saveIfAbsent(any());
    }

    @Test
    @DisplayName("TX A — lastNotifiedAt 설정 시 해당 값이 loadSince의 since 인자로 전달된다")
    void txA_withLastNotifiedAt_passesSinceCorrectly() {
        Instant since = Instant.parse("2026-05-01T12:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(since);
        when(loadServiceChangePort.loadSince("OA-2269", since)).thenReturn(List.of());

        List<ServiceChange> result = txHelper.txA(sub, 5);

        assertThat(result).isEmpty();
        verify(loadServiceChangePort).loadSince("OA-2269", since);
    }

    @Test
    @DisplayName("TX A — 변경 없으면 빈 리스트 반환, saveIfAbsent 미호출")
    void txA_noChanges_returnsEmptyListWithoutSavingDispatch() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        when(loadServiceChangePort.loadSince(any(), any())).thenReturn(List.of());

        List<ServiceChange> result = txHelper.txA(sub, 5);

        assertThat(result).isEmpty();
        verifyNoInteractions(saveDispatchPort);
    }

    // ── TX B 성공 — last_notified_at MAX 정책 ────────────────────────────

    @Test
    @DisplayName("TX B 성공 — lastNotifiedAt=null 이면 change.changedAt으로 설정된다")
    void txBSuccess_nullLastNotifiedAt_setsChangedAt() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        Instant changedAt = Instant.parse("2026-05-15T09:00:00Z");
        ServiceChange change = changeAt(changedAt);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, change, "제목", "본문", TemplateSource.AI);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(changedAt);
    }

    @Test
    @DisplayName("TX B 성공 — change.changedAt > lastNotifiedAt 이면 lastNotifiedAt이 전진한다")
    void txBSuccess_changedAtAfterLastNotifiedAt_advancesLastNotifiedAt() {
        Instant existing = Instant.parse("2026-05-10T09:00:00Z");
        Instant newer = Instant.parse("2026-05-15T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(existing);
        ServiceChange change = changeAt(newer);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, change, "제목", "본문", TemplateSource.AI);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(newer);
    }

    @Test
    @DisplayName("TX B 성공 — change.changedAt < lastNotifiedAt 이면 lastNotifiedAt을 갱신하지 않는다")
    void txBSuccess_changedAtBeforeLastNotifiedAt_keepsExistingLastNotifiedAt() {
        Instant existing = Instant.parse("2026-05-15T09:00:00Z");
        Instant older = Instant.parse("2026-05-10T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(existing);
        ServiceChange change = changeAt(older);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, change, "제목", "본문", TemplateSource.AI);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(existing);
    }

    @Test
    @DisplayName("TX B 성공 — dispatch.markSuccess가 호출되고 save가 호출된다")
    void txBSuccess_marksDispatchSuccessAndSaves() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);
        when(saveSubscriptionPort.save(any())).thenReturn(sub);

        txHelper.txBSuccess(dispatch, sub, change, "제목", "본문", TemplateSource.FALLBACK);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("제목");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.FALLBACK);
        verify(saveDispatchPort).save(dispatch);
        verify(saveSubscriptionPort).save(sub);
    }

    // ── TX B 실패 ─────────────────────────────────────────────────────────

    @Test
    @DisplayName("TX B 실패 — dispatch.markFailed 호출 후 FAILED 상태로 save (4번째 실패)")
    void txBFailure_fourthAttempt_savesAsFailed() {
        NotificationDispatch dispatch = pendingDispatch();
        // 3번 선행 실패 (attempt=3, FAILED)
        dispatch.markFailed("이전 오류", 5);
        dispatch.markFailed("이전 오류", 5);
        dispatch.markFailed("이전 오류", 5);
        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBFailure(dispatch, "새 오류", 5);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 4);
        verify(saveDispatchPort).save(dispatch);
    }

    @Test
    @DisplayName("TX B 실패 — 5번째 실패 시 DEAD로 전환되고 save 호출")
    void txBFailure_fifthAttempt_savesAsDead() {
        NotificationDispatch dispatch = pendingDispatch();
        // 4번 선행 실패 (attempt=4, FAILED)
        for (int i = 0; i < 4; i++) {
            dispatch.markFailed("이전 오류", 5);
        }
        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBFailure(dispatch, "최종 오류", 5);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 5);
        verify(saveDispatchPort).save(dispatch);
    }

    @Test
    @DisplayName("TX B 실패 — subscription의 lastNotifiedAt을 갱신하지 않는다")
    void txBFailure_doesNotModifySubscriptionLastNotifiedAt() {
        Instant original = Instant.parse("2026-05-10T09:00:00Z");
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(original);
        NotificationDispatch dispatch = pendingDispatch();
        when(saveDispatchPort.save(any())).thenReturn(dispatch);

        txHelper.txBFailure(dispatch, "오류", 5);

        // saveSubscriptionPort.save()는 절대 호출되면 안 된다
        verifyNoInteractions(saveSubscriptionPort);
        // 구독 객체의 lastNotifiedAt도 변경 없음
        assertThat(sub.getLastNotifiedAt()).isEqualTo(original);
    }

    // ── TX A — saveIfAbsent 멱등성 ────────────────────────────────────────

    @Test
    @DisplayName("TX A — saveIfAbsent가 empty를 반환해도 txA 자체는 변경 목록을 정상 반환한다")
    void txA_saveIfAbsentReturnsEmpty_stillReturnsChanges() {
        NotificationSubscription sub = subscriptionWithLastNotifiedAt(null);
        ServiceChange change = changeAt(Instant.now());
        when(loadServiceChangePort.loadSince(any(), any())).thenReturn(List.of(change));
        // 중복으로 인해 empty (멱등 경로)
        when(saveDispatchPort.saveIfAbsent(any())).thenReturn(Optional.empty());

        List<ServiceChange> result = txHelper.txA(sub, 5);

        assertThat(result).hasSize(1);
    }
}
