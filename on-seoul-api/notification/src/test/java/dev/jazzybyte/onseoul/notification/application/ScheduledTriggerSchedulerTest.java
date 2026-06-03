package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.adapter.out.persistence.NotificationContentSerializer;
import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.TriggerType;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ScheduledTriggerSchedulerTest {

    @Mock private LoadSubscriptionPort loadSubscriptionPort;
    @Mock private LoadScheduledTriggerPort loadScheduledTriggerPort;
    @Mock private LoadDispatchPort loadDispatchPort;
    @Mock private SubscriptionFilterParserPort filterParser;
    @Mock private LoadUserContactPort loadUserContactPort;
    @Mock private TemplateGenerationPort templateGenerationPort;
    @Mock private PushNotificationPort pushNotificationPort;
    @Mock private SaveBatchPort saveBatchPort;
    @Mock private NotificationTxHelper txHelper;

    private ScheduledTriggerScheduler scheduler;

    private static final LocalDate TODAY = LocalDate.of(2026, 6, 3);
    private static final UserContact CONTACT = new UserContact(1L, "u@example.com", "+8210");

    @BeforeEach
    void setUp() {
        scheduler = new ScheduledTriggerScheduler(
                loadSubscriptionPort, loadScheduledTriggerPort, loadDispatchPort, filterParser,
                loadUserContactPort, templateGenerationPort, pushNotificationPort,
                new NotificationContentSerializer(new com.fasterxml.jackson.databind.ObjectMapper()),
                saveBatchPort, txHelper, Clock.systemUTC());

        lenient().when(filterParser.parse(any())).thenReturn(SubscriptionFilter.empty());
        // 기본: 두 dedup 선조회 모두 미존재(발행 진행).
        lenient().when(loadDispatchPort.existsChangeDispatchForServiceToday(anyLong(), any(), any()))
                .thenReturn(false);
        lenient().when(loadDispatchPort.existsScheduledDispatch(anyLong(), any(), any()))
                .thenReturn(false);
        lenient().when(loadUserContactPort.loadContact(anyLong())).thenReturn(Optional.of(CONTACT));
        lenient().when(loadScheduledTriggerPort.loadOpeningToday(any(), any())).thenReturn(List.of());
        lenient().when(loadScheduledTriggerPort.loadReceiptStartTomorrow(any(), any())).thenReturn(List.of());
        lenient().when(loadScheduledTriggerPort.loadDeadlineToday(any(), any())).thenReturn(List.of());
        lenient().when(loadSubscriptionPort.loadChunk(anyLong(), anyInt())).thenReturn(List.of());
        lenient().when(saveBatchPort.insertRunning(any())).thenAnswer(inv ->
                new NotificationBatch(7L, java.time.Instant.now(), null, BatchStatus.RUNNING, null, null));
        lenient().when(saveBatchPort.update(any())).thenAnswer(inv -> inv.getArgument(0));
        lenient().when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "요약", TemplateSource.AI));
        // saveScheduledIfAbsent: 기본은 고정 PENDING dispatch 반환(첫 발행).
        // 인자를 echo 하지 않는다 — re-stub 시 lambda 가 null 인자로 재실행되어 NPE 나는 것을 피한다.
        lenient().when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.of(
                NotificationDispatch.createScheduled(7L, 1L, TriggerType.DEADLINE_DDAY, "OA-default", TODAY)));
    }

    private NotificationSubscription sub(long id) {
        return NotificationSubscription.ofPersistence(id, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, java.time.Instant.now());
    }

    private ScheduledServiceMatch match(String serviceId) {
        return new ScheduledServiceMatch(serviceId, serviceId + "-name", null, null,
                null, "강남구", "접수중", null, null, "2026-06-03T00:00Z");
    }

    @Test
    @DisplayName("매칭 service마다 시점 dispatch를 발행하고 푸시 발송한다")
    void dispatchesPerMatchedService() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1"), match("OA-2")));

        scheduler.processAll(TODAY);

        verify(pushNotificationPort, org.mockito.Mockito.times(2))
                .send(any(UserContact.class), any(NotificationContent.class), any(), any());
        verify(txHelper, org.mockito.Mockito.times(2))
                .txBScheduledSuccess(any(), eq("제목"), eq("요약"), eq(TemplateSource.AI));
    }

    @Test
    @DisplayName("dedup으로 saveScheduledIfAbsent가 empty면 발송하지 않는다")
    void dedupSkipped_noSend() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.empty());

        scheduler.processAll(TODAY);

        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(txHelper, never()).txBScheduledSuccess(any(), any(), any(), any());
    }

    @Test
    @DisplayName("CHANGE cross-dedup 히트 → 시점 발행 skip (batch 미생성, 발송 없음)")
    void crossDedupHit_skipsWithoutBatch() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(loadDispatchPort.existsChangeDispatchForServiceToday(eq(1L), eq("OA-1"), eq(TODAY)))
                .thenReturn(true);

        scheduler.processAll(TODAY);

        // batch 를 만들지 않고, dispatch 발행/발송도 하지 않는다(빈 batch 미생성).
        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(saveBatchPort, never()).insertRunning(any());
        verify(txHelper, never()).saveScheduledIfAbsent(any());
    }

    @Test
    @DisplayName("시점-시점 dedup 선조회 히트 → 시점 발행 skip (batch 미생성)")
    void scheduledDedupHit_skipsWithoutBatch() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(loadDispatchPort.existsScheduledDispatch(eq(1L), eq("OA-1"), eq(TODAY)))
                .thenReturn(true);

        scheduler.processAll(TODAY);

        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(saveBatchPort, never()).insertRunning(any());
        verify(txHelper, never()).saveScheduledIfAbsent(any());
    }

    @Test
    @DisplayName("trigger_type이 템플릿 요청에 전달된다 (DEADLINE_DDAY)")
    void triggerTypePassedToTemplateRequest() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));

        scheduler.processAll(TODAY);

        ArgumentCaptor<NotificationTemplateRequest> captor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(captor.capture());
        assertThat(captor.getValue().triggerType()).isEqualTo(TriggerType.DEADLINE_DDAY);
        // 시점 트리거는 changes 빈 배열
        assertThat(captor.getValue().services().get(0).changes()).isEmpty();
    }

    @Test
    @DisplayName("push 실패 시 txBScheduledFailure 호출 (발송 카운트 미증가)")
    void pushFails_callsScheduledFailure() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), any(NotificationContent.class), any(), any());

        scheduler.processAll(TODAY);

        verify(txHelper).txBScheduledFailure(any(), eq("제목"), eq("요약"), eq(TemplateSource.AI), eq("Knock 오류"));
    }

    @Test
    @DisplayName("세 트리거(개시/D-1/마감)를 모두 조회한다 — status 필터 무시는 어댑터 책임이므로 호출만 검증")
    void allThreeTriggersQueried() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));

        scheduler.processAll(TODAY);

        verify(loadScheduledTriggerPort).loadOpeningToday(any(), eq(TODAY));
        verify(loadScheduledTriggerPort).loadReceiptStartTomorrow(any(), eq(TODAY));
        verify(loadScheduledTriggerPort).loadDeadlineToday(any(), eq(TODAY));
    }

    @Test
    @DisplayName("한 구독 처리 실패는 삼켜지고 다음 구독은 정상 처리된다")
    void subscriptionFailureSwallowed_continues() {
        NotificationSubscription s1 = sub(1L);
        NotificationSubscription s2 = sub(2L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s1, s2));
        // s1 처리 중 filterParser 예외
        when(filterParser.parse(any()))
                .thenThrow(new RuntimeException("파싱 실패"))
                .thenReturn(SubscriptionFilter.empty());
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-2")));

        scheduler.processAll(TODAY);

        // s2는 정상 발송
        verify(pushNotificationPort).send(any(), any(NotificationContent.class), any(), any());
    }

    @Test
    @DisplayName("dispatch에 직렬화 payload가 할당되어 발송된다")
    void payloadAssigned() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        ScheduledServiceMatch m = match("OA-1");
        NotificationDispatch persisted = NotificationDispatch.createScheduled(
                7L, 1L, TriggerType.DEADLINE_DDAY, "OA-1", TODAY);
        when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.of(persisted));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY))).thenReturn(List.of(m));

        scheduler.processAll(TODAY);

        assertThat(persisted.getNotificationPayload()).isNotNull();
        assertThat(persisted.getNotificationPayload()).contains("\"services\"");
    }
}
