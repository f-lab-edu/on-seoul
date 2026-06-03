package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase.DispatchPage;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class NotificationDispatchServiceTest {

    private final LoadDispatchPort loadPort = mock(LoadDispatchPort.class);
    private final NotificationDispatchService service = new NotificationDispatchService(loadPort);

    private NotificationDispatch dispatch(long id) {
        return new NotificationDispatch(id, 1L, 10L,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.CHANGE, null, null,
                DispatchStatus.SUCCESS,
                Instant.now(), "t", "b", null, null, 0, null, Instant.now(), Instant.now());
    }

    @Test
    @DisplayName("list() — 결과가 size 와 같으면 nextCursor 는 마지막 dispatch id")
    void list_fullPage_returnsNextCursor() {
        when(loadPort.loadByUserId(eq(1L), eq(null), eq(2)))
                .thenReturn(List.of(dispatch(100L), dispatch(90L)));

        DispatchPage page = service.list(1L, null, 2);

        assertThat(page.dispatches()).hasSize(2);
        assertThat(page.nextCursor()).isEqualTo(90L);
    }

    @Test
    @DisplayName("list() — 결과가 size 보다 작으면 nextCursor 는 null")
    void list_partialPage_returnsNullCursor() {
        when(loadPort.loadByUserId(eq(1L), eq(null), eq(20)))
                .thenReturn(List.of(dispatch(5L)));

        DispatchPage page = service.list(1L, null, 20);

        assertThat(page.nextCursor()).isNull();
    }

    @Test
    @DisplayName("list() — size=0 이면 기본값 20 적용")
    void list_zeroSize_usesDefault() {
        when(loadPort.loadByUserId(eq(1L), eq(null), eq(20)))
                .thenReturn(List.of());
        DispatchPage page = service.list(1L, null, 0);
        assertThat(page.dispatches()).isEmpty();
    }

    @Test
    @DisplayName("list() — size 가 100 초과면 INVALID_INPUT")
    void list_sizeOverMax_throws400() {
        assertThatThrownBy(() -> service.list(1L, null, 101))
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
    }
}
