package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ResilientPushNotificationAdapterTest {

    private PushNotificationPort primary;
    private FallbackNotificationPort fallback;
    private MeterRegistry meterRegistry;
    private ResilientPushNotificationAdapter adapter;

    private final UserContact recipient = new UserContact(1L, "test@example.com", "010-1234-5678");
    private final Set<NotificationChannel> channels = Set.of(NotificationChannel.EMAIL);

    @BeforeEach
    void setUp() {
        primary = mock(PushNotificationPort.class);
        fallback = mock(FallbackNotificationPort.class);
        meterRegistry = new SimpleMeterRegistry();
        adapter = new ResilientPushNotificationAdapter(primary, fallback, meterRegistry);
    }

    @Test
    @DisplayName("1차 발송 성공 시 fallback을 호출하지 않는다")
    void send_primarySucceeds_fallbackNotCalled() {
        doNothing().when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        verify(primary).send(recipient, "제목", "본문", 10L, channels);
        verifyNoInteractions(fallback);
    }

    @Test
    @DisplayName("1차 발송 실패 시 fallback을 호출한다")
    void send_primaryThrows_fallbackCalled() {
        RuntimeException knock = new RuntimeException("connection refused");
        doThrow(knock).when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        verify(fallback).sendFallback(
                eq(recipient), eq("제목"), eq("본문"), eq(10L),
                eq(channels), any(FallbackReason.class), eq(knock));
    }

    @Test
    @DisplayName("KnockDispatchException(KNOCK_TIMEOUT) → FallbackReason.KNOCK_TIMEOUT")
    void send_knockTimeoutException_classifiesAsKnockTimeout() {
        doThrow(new KnockDispatchException(FallbackReason.KNOCK_TIMEOUT, "타임아웃", null))
                .when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), anyString(), anyString(), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_TIMEOUT);
    }

    @Test
    @DisplayName("일반 RuntimeException → FallbackReason.KNOCK_UNAVAILABLE")
    void send_genericException_classifiesAsKnockUnavailable() {
        doThrow(new RuntimeException("unknown error"))
                .when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), anyString(), anyString(), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_UNAVAILABLE);
    }

    @Test
    @DisplayName("1차 발송 실패 시 fallback 이유별 metric이 기록된다")
    void send_primaryFails_metricIncremented() {
        doThrow(new RuntimeException("connection refused"))
                .when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        double count = meterRegistry.counter("notification.push.fallback",
                "reason", FallbackReason.KNOCK_UNAVAILABLE.name()).count();
        assertThat(count).isEqualTo(1.0);
    }

    @Test
    @DisplayName("KnockDispatchException(KNOCK_SERVER_ERROR) → FallbackReason.KNOCK_SERVER_ERROR")
    void send_knockServerErrorException_classifiesAsKnockServerError() {
        doThrow(new KnockDispatchException(FallbackReason.KNOCK_SERVER_ERROR, "5xx 오류", null))
                .when(primary).send(any(), anyString(), anyString(), anyLong(), anySet());

        adapter.send(recipient, "제목", "본문", 10L, channels);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), anyString(), anyString(), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_SERVER_ERROR);
    }
}
