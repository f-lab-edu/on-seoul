package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.port.out.AiCacheFlushPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort.ChangedServiceIds;
import dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AiCacheFlushHandlerTest {

    @Mock private LoadChangedServiceIdsPort loadChangedServiceIdsPort;
    @Mock private AiCacheFlushPort aiCacheFlushPort;

    private AiCacheFlushHandler handler;

    private final EmbeddingSyncCompletedEvent event =
            new EmbeddingSyncCompletedEvent(Instant.parse("2026-01-01T00:00:00Z"));

    @BeforeEach
    void setUp() {
        handler = new AiCacheFlushHandler(loadChangedServiceIdsPort, aiCacheFlushPort);
    }

    @Test
    @DisplayName("이번 사이클에 변경이 있으면(>0) flush를 1회 호출한다")
    void changesPresent_flushesOnce() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of("SVC-1"), List.of()));

        handler.onEmbeddingSyncCompleted(event);

        verify(aiCacheFlushPort).flush();
    }

    @Test
    @DisplayName("DELETE만 있어도(삭제 포함) flush를 호출한다")
    void deleteOnly_flushes() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of(), List.of("SVC-9")));

        handler.onEmbeddingSyncCompleted(event);

        verify(aiCacheFlushPort).flush();
    }

    @Test
    @DisplayName("변경이 0이면 flush를 호출하지 않는다 (thundering herd 방지)")
    void noChanges_doesNotFlush() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of(), List.of()));

        handler.onEmbeddingSyncCompleted(event);

        verify(aiCacheFlushPort, never()).flush();
    }

    @Test
    @DisplayName("runStartedAt을 그대로 loadSince에 전달한다 (이번 사이클 경계)")
    void passesRunStartedAtToLoadSince() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of(), List.of()));

        handler.onEmbeddingSyncCompleted(event);

        verify(loadChangedServiceIdsPort).loadSince(Instant.parse("2026-01-01T00:00:00Z"));
    }

    @Test
    @DisplayName("flush 호출이 실패(타임아웃/5xx/401)해도 예외를 전파하지 않는다 (best-effort)")
    void flushFailure_doesNotPropagate() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of("SVC-1"), List.of()));
        doThrow(new RuntimeException("AI down")).when(aiCacheFlushPort).flush();

        assertThatCode(() -> handler.onEmbeddingSyncCompleted(event)).doesNotThrowAnyException();
    }

    @Test
    @DisplayName("변경 조회 자체가 실패해도 예외를 전파하지 않고 flush도 호출하지 않는다")
    void loadFailure_doesNotPropagate() {
        when(loadChangedServiceIdsPort.loadSince(any())).thenThrow(new RuntimeException("DB down"));

        assertThatCode(() -> handler.onEmbeddingSyncCompleted(event)).doesNotThrowAnyException();
        verify(aiCacheFlushPort, never()).flush();
    }
}
