package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
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

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class NotificationSchedulerTest {

    @Mock
    private LoadSubscriptionPort loadSubscriptionPort;

    @Mock
    private LoadDispatchPort loadDispatchPort;

    @Mock
    private TemplateGenerationPort templateGenerationPort;

    @Mock
    private PushNotificationPort pushNotificationPort;

    @Mock
    private NotificationTxHelper txHelper;

    private SimpleMeterRegistry meterRegistry;

    private NotificationScheduler scheduler;

    @BeforeEach
    void setUp() {
        meterRegistry = new SimpleMeterRegistry();
        scheduler = new NotificationScheduler(
                loadSubscriptionPort, loadDispatchPort,
                templateGenerationPort, pushNotificationPort,
                txHelper, meterRegistry);
    }

    private NotificationSubscription stubSubscription(Long id, String serviceId) {
        return new NotificationSubscription(id, 1L, serviceId, "{}",
                Set.of(NotificationChannel.EMAIL), null, Instant.now());
    }

    private ServiceChange stubChange(Long id, String serviceId) {
        return new ServiceChange(id, serviceId, "CHANGED", "status", "OPEN", "CLOSED", Instant.now());
    }

    private NotificationDispatch stubDispatch(Long id, Long subId, Long changeId) {
        return NotificationDispatch.create(subId, changeId);
    }

    @Test
    @DisplayName("구독이 없으면 처리 없이 종료된다")
    void processAllSubscriptions_noSubscriptions_nothingProcessed() throws InterruptedException {
        when(loadSubscriptionPort.loadAll()).thenReturn(List.of());

        scheduler.processAllSubscriptions();
        Thread.sleep(100);

        verifyNoInteractions(txHelper, loadDispatchPort, templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("TX A가 빈 목록을 반환하면 template/push를 호출하지 않는다")
    void processSubscription_txAReturnsEmpty_noTemplatePush() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of());

        scheduler.processAllSubscriptions();
        Thread.sleep(200);

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("retryable dispatch가 없으면 template/push를 건너뛴다")
    void processChange_noRetryableDispatch_skips() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        ServiceChange change = stubChange(100L, "OA-2269");

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of(change));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.empty());

        scheduler.processAllSubscriptions();
        Thread.sleep(200);

        verifyNoInteractions(templateGenerationPort, pushNotificationPort);
    }

    @Test
    @DisplayName("정상 흐름: template 생성 후 push 발송, txBSuccess 호출")
    void processChange_successPath_callsTxBSuccess() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        ServiceChange change = stubChange(100L, "OA-2269");
        NotificationDispatch dispatch = stubDispatch(10L, 1L, 100L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.AI);

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of(change));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.of(dispatch));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();
        Thread.sleep(300);

        verify(pushNotificationPort).send(anyLong(), anyString(), anyString(), any(), any());
        verify(txHelper).txBSuccess(eq(dispatch), eq(sub), eq(change),
                eq("제목"), eq("본문"), eq(TemplateSource.AI));
    }

    @Test
    @DisplayName("push 발송 실패 시 txBFailure 호출")
    void processChange_pushFails_callsTxBFailure() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        ServiceChange change = stubChange(100L, "OA-2269");
        NotificationDispatch dispatch = stubDispatch(10L, 1L, 100L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.FALLBACK);

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of(change));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.of(dispatch));
        when(templateGenerationPort.generate(any())).thenReturn(template);
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(anyLong(), anyString(), anyString(), any(), any());

        scheduler.processAllSubscriptions();
        Thread.sleep(300);

        verify(txHelper).txBFailure(eq(dispatch), eq("Knock 오류"), eq(NotificationScheduler.MAX_ATTEMPTS));
    }

    @Test
    @DisplayName("TX A 실패 시 warn 로그만 남기고 다음 구독 처리")
    void processSubscription_txAThrows_continuesNextSubscription() throws InterruptedException {
        NotificationSubscription sub1 = stubSubscription(1L, "OA-2269");
        NotificationSubscription sub2 = stubSubscription(2L, "OA-2266");
        ServiceChange change2 = stubChange(200L, "OA-2266");
        NotificationDispatch dispatch2 = stubDispatch(20L, 2L, 200L);
        TemplateResult template = new TemplateResult("제목2", "본문2", TemplateSource.AI);

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub1, sub2));
        when(txHelper.txA(eq(sub1), anyInt())).thenThrow(new RuntimeException("DB 오류"));
        when(txHelper.txA(eq(sub2), anyInt())).thenReturn(List.of(change2));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.of(dispatch2));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();
        Thread.sleep(400);

        // sub2는 정상 처리됨
        verify(pushNotificationPort).send(anyLong(), anyString(), anyString(), any(), any());
    }

    @Test
    @DisplayName("정상 발송 시 notification.dispatch.attempts{result=success} 카운터가 증가한다")
    void processChange_successPath_incrementsSuccessCounter() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        ServiceChange change = stubChange(100L, "OA-2269");
        NotificationDispatch dispatch = stubDispatch(10L, 1L, 100L);
        TemplateResult template = new TemplateResult("제목", "본문", TemplateSource.AI);

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of(change));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.of(dispatch));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();
        Thread.sleep(300);

        double count = meterRegistry.counter("notification.dispatch.attempts", "result", "success").count();
        assertThat(count).isEqualTo(1.0);
    }

    @Test
    @DisplayName("templateGenerationPort에 올바른 NotificationTemplateRequest가 전달된다")
    void processChange_templateRequestContainsChangeFields() throws InterruptedException {
        NotificationSubscription sub = stubSubscription(1L, "OA-2269");
        ServiceChange change = new ServiceChange(100L, "OA-2269", "CHANGED", "svcStatus", "OPEN", "CLOSED", Instant.now());
        NotificationDispatch dispatch = stubDispatch(10L, 1L, 100L);
        TemplateResult template = new TemplateResult("t", "b", TemplateSource.AI);

        when(loadSubscriptionPort.loadAll()).thenReturn(List.of(sub));
        when(txHelper.txA(any(), anyInt())).thenReturn(List.of(change));
        when(loadDispatchPort.loadRetryable(anyLong(), anyLong(), anyInt()))
                .thenReturn(Optional.of(dispatch));
        when(templateGenerationPort.generate(any())).thenReturn(template);

        scheduler.processAllSubscriptions();
        Thread.sleep(300);

        ArgumentCaptor<NotificationTemplateRequest> captor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(captor.capture());
        NotificationTemplateRequest req = captor.getValue();

        assertThat(req.serviceId()).isEqualTo("OA-2269");
        assertThat(req.changeType()).isEqualTo("CHANGED");
        assertThat(req.fieldName()).isEqualTo("svcStatus");
        assertThat(req.oldValue()).isEqualTo("OPEN");
        assertThat(req.newValue()).isEqualTo("CLOSED");
    }
}
