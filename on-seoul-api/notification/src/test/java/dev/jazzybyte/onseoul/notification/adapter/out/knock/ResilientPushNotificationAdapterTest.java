package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.FallbackNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import io.github.resilience4j.circuitbreaker.CallNotPermittedException;
import io.github.resilience4j.circuitbreaker.CircuitBreaker;
import io.github.resilience4j.circuitbreaker.CircuitBreakerConfig;
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.Duration;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class ResilientPushNotificationAdapterTest {

    private PushNotificationPort primary;
    private FallbackNotificationPort fallback;
    private MeterRegistry meterRegistry;
    private CircuitBreakerRegistry circuitBreakerRegistry;
    private ResilientPushNotificationAdapter adapter;

    private final UserContact recipient = new UserContact(1L, "test@example.com", "010-1234-5678");
    private final Set<NotificationChannel> channels = Set.of(NotificationChannel.EMAIL);
    private final NotificationContent content =
            new NotificationContent("제목", "요약", java.util.List.of());

    @BeforeEach
    void setUp() {
        primary = mock(PushNotificationPort.class);
        fallback = mock(FallbackNotificationPort.class);
        meterRegistry = new SimpleMeterRegistry();
        // 테스트용 서킷 브레이커: 슬라이딩 윈도우 3, 실패율 50% 초과 시 OPEN
        // 테스트용 서킷 브레이커: 슬라이딩 윈도우 3, minimumNumberOfCalls 3, 실패율 50% 초과 시 OPEN
        // CircuitBreakerRegistry.of(config)로 커스텀 config를 기본값으로 사용해 모든 인스턴스에 적용.
        CircuitBreakerConfig config = CircuitBreakerConfig.custom()
                .slidingWindowType(CircuitBreakerConfig.SlidingWindowType.COUNT_BASED)
                .slidingWindowSize(3)
                .minimumNumberOfCalls(3)  // 기본값 100 → 3으로 재정의 (테스트용)
                .failureRateThreshold(50f)
                .waitDurationInOpenState(Duration.ofSeconds(60))
                .build();
        circuitBreakerRegistry = CircuitBreakerRegistry.of(config);
        adapter = new ResilientPushNotificationAdapter(
                primary, fallback, circuitBreakerRegistry, meterRegistry);
    }

    @Test
    @DisplayName("1차 발송 성공 시 fallback을 호출하지 않고 예외도 없다")
    void send_primarySucceeds_noFallbackNoException() {
        doNothing().when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        adapter.send(recipient, content, 10L, channels);

        verify(primary).send(recipient, content, 10L, channels);
        verifyNoInteractions(fallback);
    }

    @Test
    @DisplayName("1차 발송 실패 시 fallback을 호출하고 예외를 rethrow한다")
    void send_primaryThrows_fallbackCalledAndExceptionRethrown() {
        RuntimeException knock = new RuntimeException("connection refused");
        doThrow(knock).when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        assertThatThrownBy(() -> adapter.send(recipient, content, 10L, channels))
                .isSameAs(knock);

        verify(fallback).sendFallback(
                eq(recipient), eq(content), eq(10L),
                eq(channels), any(FallbackReason.class), eq(knock));
    }

    @Test
    @DisplayName("KnockDispatchException(KNOCK_TIMEOUT) → FallbackReason.KNOCK_TIMEOUT으로 분류")
    void send_knockTimeoutException_classifiesAsKnockTimeout() {
        doThrow(new KnockDispatchException(FallbackReason.KNOCK_TIMEOUT, "타임아웃", null))
                .when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        assertThatThrownBy(() -> adapter.send(recipient, content, 10L, channels))
                .isInstanceOf(KnockDispatchException.class);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), any(NotificationContent.class), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_TIMEOUT);
    }

    @Test
    @DisplayName("일반 RuntimeException → FallbackReason.KNOCK_UNAVAILABLE로 분류")
    void send_genericException_classifiesAsKnockUnavailable() {
        doThrow(new RuntimeException("unknown error"))
                .when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        assertThatThrownBy(() -> adapter.send(recipient, content, 10L, channels))
                .isInstanceOf(RuntimeException.class);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), any(NotificationContent.class), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_UNAVAILABLE);
    }

    @Test
    @DisplayName("1차 발송 실패 시 fallback 이유별 metric이 기록된다")
    void send_primaryFails_metricIncremented() {
        doThrow(new RuntimeException("connection refused"))
                .when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        assertThatThrownBy(() -> adapter.send(recipient, content, 10L, channels));

        double count = meterRegistry.counter("notification.push.fallback",
                "reason", FallbackReason.KNOCK_UNAVAILABLE.name()).count();
        assertThat(count).isEqualTo(1.0);
    }

    @Test
    @DisplayName("KnockDispatchException(KNOCK_SERVER_ERROR) → FallbackReason.KNOCK_SERVER_ERROR로 분류")
    void send_knockServerErrorException_classifiesAsKnockServerError() {
        doThrow(new KnockDispatchException(FallbackReason.KNOCK_SERVER_ERROR, "5xx 오류", null))
                .when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        assertThatThrownBy(() -> adapter.send(recipient, content, 10L, channels))
                .isInstanceOf(KnockDispatchException.class);

        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback).sendFallback(any(), any(NotificationContent.class), anyLong(), anySet(),
                reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getValue()).isEqualTo(FallbackReason.KNOCK_SERVER_ERROR);
    }

    @Test
    @DisplayName("서킷 오픈 상태에서 CallNotPermittedException → KNOCK_CIRCUIT_OPEN 분류 후 rethrow")
    void send_circuitOpen_callNotPermittedExceptionRethrownWithCorrectReason() {
        // 슬라이딩 윈도우 3, 실패율 50% 초과 → 윈도우를 채운 뒤 OPEN 유도
        // COUNT_BASED 윈도우는 minimumNumberOfCalls(= windowSize = 3)를 채워야 실패율을 계산한다.
        // 3회 모두 실패 → 실패율 100% > 50% → OPEN 전환
        RuntimeException knockEx = new KnockDispatchException(
                FallbackReason.KNOCK_UNAVAILABLE, "down", null);
        doThrow(knockEx).when(primary).send(any(), any(NotificationContent.class), anyLong(), anySet());

        for (int i = 0; i < 3; i++) {
            try { adapter.send(recipient, content, (long) i, channels); } catch (Exception ignored) {}
        }

        CircuitBreaker cb = circuitBreakerRegistry.circuitBreaker(
                ResilientPushNotificationAdapter.CIRCUIT_BREAKER_NAME);
        assertThat(cb.getState()).isEqualTo(CircuitBreaker.State.OPEN);

        // OPEN 상태에서 발송 시도 → CallNotPermittedException rethrow
        assertThatThrownBy(() -> adapter.send(recipient, content, 99L, channels))
                .isInstanceOf(CallNotPermittedException.class);

        // KNOCK_CIRCUIT_OPEN 이유로 fallback 호출 확인
        ArgumentCaptor<FallbackReason> reasonCaptor = ArgumentCaptor.forClass(FallbackReason.class);
        verify(fallback, atLeastOnce()).sendFallback(any(), any(NotificationContent.class), eq(99L),
                anySet(), reasonCaptor.capture(), any());
        assertThat(reasonCaptor.getAllValues()).contains(FallbackReason.KNOCK_CIRCUIT_OPEN);
    }
}
