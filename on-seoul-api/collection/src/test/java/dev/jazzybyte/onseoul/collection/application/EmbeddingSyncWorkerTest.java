package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.application.EmbeddingSyncWorker.Chunk;
import dev.jazzybyte.onseoul.collection.port.out.EmbeddingSyncPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort.ChangedServiceIds;
import dev.jazzybyte.onseoul.event.CollectionCompletedEvent;
import dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class EmbeddingSyncWorkerTest {

    @Mock private LoadChangedServiceIdsPort loadChangedServiceIdsPort;
    @Mock private EmbeddingSyncPort embeddingSyncPort;
    @Mock private ApplicationEventPublisher eventPublisher;

    private EmbeddingSyncWorker worker;

    private final CollectionCompletedEvent event =
            new CollectionCompletedEvent(Instant.parse("2026-01-01T00:00:00Z"));

    @BeforeEach
    void setUp() {
        worker = new EmbeddingSyncWorker(loadChangedServiceIdsPort, embeddingSyncPort, eventPublisher);
    }

    @Test
    @DisplayName("변경 service_id가 없으면 AI를 호출하지 않고 EmbeddingSyncCompletedEvent를 발행한다")
    void noChanges_skipsAiAndPublishesEvent() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of(), List.of()));

        worker.onCollectionCompleted(event);

        verify(embeddingSyncPort, never()).sync(anyList(), anyList());
        verify(eventPublisher).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("upsert/delete를 정확히 분류해 AI 한 번 호출하고 이벤트를 발행한다")
    void classifiesUpsertAndDelete_singleCall() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of("SVC-1", "SVC-2"), List.of("SVC-9")));

        worker.onCollectionCompleted(event);

        ArgumentCaptor<List<String>> upsertCaptor = ArgumentCaptor.forClass(List.class);
        ArgumentCaptor<List<String>> deleteCaptor = ArgumentCaptor.forClass(List.class);
        verify(embeddingSyncPort).sync(upsertCaptor.capture(), deleteCaptor.capture());
        assertThat(upsertCaptor.getValue()).containsExactly("SVC-1", "SVC-2");
        assertThat(deleteCaptor.getValue()).containsExactly("SVC-9");
        verify(eventPublisher).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("loadSince에 이벤트의 runStartedAt을 그대로 전달한다")
    void passesRunStartedAtToLoadSince() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of(), List.of()));

        worker.onCollectionCompleted(event);

        verify(loadChangedServiceIdsPort).loadSince(Instant.parse("2026-01-01T00:00:00Z"));
    }

    @Test
    @DisplayName("upsert+delete 합계가 500을 넘으면 500 이하 청크로 분할해 여러 번 호출한다")
    void chunkedWhenOver500() {
        List<String> upsert = IntStream.range(0, 600).mapToObj(i -> "U-" + i).toList();
        List<String> delete = IntStream.range(0, 50).mapToObj(i -> "D-" + i).toList();
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(upsert, delete));

        worker.onCollectionCompleted(event);

        // 650건 → 500 + 150 = 2회 호출
        ArgumentCaptor<List<String>> upsertCaptor = ArgumentCaptor.forClass(List.class);
        ArgumentCaptor<List<String>> deleteCaptor = ArgumentCaptor.forClass(List.class);
        verify(embeddingSyncPort, times(2)).sync(upsertCaptor.capture(), deleteCaptor.capture());

        for (int i = 0; i < upsertCaptor.getAllValues().size(); i++) {
            int total = upsertCaptor.getAllValues().get(i).size() + deleteCaptor.getAllValues().get(i).size();
            assertThat(total).isLessThanOrEqualTo(EmbeddingSyncWorker.MAX_BATCH);
        }
        // 전체 합산이 보존되어야 한다
        int totalUpsert = upsertCaptor.getAllValues().stream().mapToInt(List::size).sum();
        int totalDelete = deleteCaptor.getAllValues().stream().mapToInt(List::size).sum();
        assertThat(totalUpsert).isEqualTo(600);
        assertThat(totalDelete).isEqualTo(50);
        verify(eventPublisher).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("AI 호출이 실패해도 예외를 전파하지 않고 EmbeddingSyncCompletedEvent를 발행한다")
    void aiFailure_stillPublishesEvent() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(List.of("SVC-1"), List.of()));
        doThrow(new RuntimeException("AI down")).when(embeddingSyncPort).sync(anyList(), anyList());

        worker.onCollectionCompleted(event);

        verify(eventPublisher).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("loadSince 자체가 실패해도 EmbeddingSyncCompletedEvent를 발행한다")
    void loadFailure_stillPublishesEvent() {
        when(loadChangedServiceIdsPort.loadSince(any())).thenThrow(new RuntimeException("DB down"));

        worker.onCollectionCompleted(event);

        verify(embeddingSyncPort, never()).sync(anyList(), anyList());
        verify(eventPublisher).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("upsert/delete에 겹치는 service_id가 있으면 delete에서 제거한다(upsert 우선)")
    void overlapping_removedFromDelete() {
        when(loadChangedServiceIdsPort.loadSince(any()))
                .thenReturn(new ChangedServiceIds(
                        List.of("SVC-1", "SVC-2"),
                        List.of("SVC-2", "SVC-9"))); // SVC-2가 양쪽에 존재

        worker.onCollectionCompleted(event);

        ArgumentCaptor<List<String>> upsertCaptor = ArgumentCaptor.forClass(List.class);
        ArgumentCaptor<List<String>> deleteCaptor = ArgumentCaptor.forClass(List.class);
        verify(embeddingSyncPort).sync(upsertCaptor.capture(), deleteCaptor.capture());

        assertThat(upsertCaptor.getValue()).containsExactly("SVC-1", "SVC-2");
        // SVC-2는 upsert 우선으로 delete에서 제거되어 AI 요청에 겹침이 없다
        assertThat(deleteCaptor.getValue()).containsExactly("SVC-9");
        assertThat(upsertCaptor.getValue()).doesNotContainAnyElementsOf(deleteCaptor.getValue());
    }

    @Test
    @DisplayName("중복 이벤트가 동시에 도착해도 본문(loadSince/sync)은 1회만 실행되고 완료 이벤트는 발행된다")
    void concurrentDuplicateEvents_runBodyOnce() throws Exception {
        CountDownLatch firstEntered = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        AtomicInteger loadCalls = new AtomicInteger(0);

        // 첫 호출은 진입 신호 후 release 될 때까지 sync 안에서 블로킹 → 두 번째 이벤트가 가드에 막히게 한다
        when(loadChangedServiceIdsPort.loadSince(any())).thenAnswer(inv -> {
            loadCalls.incrementAndGet();
            return new ChangedServiceIds(List.of("SVC-1"), List.of());
        });
        doAnswer(inv -> {
            firstEntered.countDown();
            releaseFirst.await(2, TimeUnit.SECONDS);
            return null;
        }).when(embeddingSyncPort).sync(anyList(), anyList());

        try (ExecutorService pool = Executors.newFixedThreadPool(2)) {
            pool.submit(() -> worker.onCollectionCompleted(event));
            assertThat(firstEntered.await(2, TimeUnit.SECONDS)).isTrue(); // 첫 워커가 sync 진입(running=true 확정)
            // 두 번째 이벤트는 가드에 막혀 본문을 건너뛰고 즉시 반환
            worker.onCollectionCompleted(event);
            releaseFirst.countDown(); // 첫 워커 진행 완료
        }

        // 본문(loadSince/sync)은 1회만 실행
        assertThat(loadCalls.get()).isEqualTo(1);
        verify(loadChangedServiceIdsPort, times(1)).loadSince(any());
        verify(embeddingSyncPort, times(1)).sync(anyList(), anyList());
        // 완료 이벤트는 진행된 워커가 발행 (가드에 막힌 워커는 발행 생략)
        verify(eventPublisher, times(1)).publishEvent(any(EmbeddingSyncCompletedEvent.class));
    }

    @Test
    @DisplayName("split() — 정확히 500이면 단일 청크다")
    void split_exactly500_singleChunk() {
        List<String> upsert = IntStream.range(0, 500).mapToObj(i -> "U-" + i).toList();
        List<Chunk> chunks = EmbeddingSyncWorker.split(upsert, List.of());
        assertThat(chunks).hasSize(1);
        assertThat(chunks.get(0).upsert()).hasSize(500);
    }

    @Test
    @DisplayName("split() — 501이면 500 + 1 두 청크로 분할된다 (경계값)")
    void split_501_splitsIntoTwoChunks() {
        List<String> upsert = IntStream.range(0, 501).mapToObj(i -> "U-" + i).toList();
        List<Chunk> chunks = EmbeddingSyncWorker.split(upsert, List.of());
        assertThat(chunks).hasSize(2);
        assertThat(chunks.get(0).upsert()).hasSize(500);
        assertThat(chunks.get(1).upsert()).hasSize(1);
        // 합산 보존
        int total = chunks.stream().mapToInt(c -> c.upsert().size() + c.delete().size()).sum();
        assertThat(total).isEqualTo(501);
    }

    @Test
    @DisplayName("split() — upsert+delete 합이 정확히 500이면 단일 청크다 (경계값, 교차 합산)")
    void split_combined500_singleChunk() {
        List<String> upsert = IntStream.range(0, 300).mapToObj(i -> "U-" + i).toList();
        List<String> delete = IntStream.range(0, 200).mapToObj(i -> "D-" + i).toList();
        List<Chunk> chunks = EmbeddingSyncWorker.split(upsert, delete);
        assertThat(chunks).hasSize(1);
        assertThat(chunks.get(0).upsert()).hasSize(300);
        assertThat(chunks.get(0).delete()).hasSize(200);
    }

    @Test
    @DisplayName("split() — upsert를 먼저 채우고 남은 용량을 delete로 채운다")
    void split_fillsUpsertThenDelete() {
        List<String> upsert = IntStream.range(0, 400).mapToObj(i -> "U-" + i).toList();
        List<String> delete = IntStream.range(0, 300).mapToObj(i -> "D-" + i).toList();

        List<Chunk> chunks = EmbeddingSyncWorker.split(upsert, delete);

        assertThat(chunks).hasSize(2);
        // 첫 청크: upsert 400 + delete 100 = 500
        assertThat(chunks.get(0).upsert()).hasSize(400);
        assertThat(chunks.get(0).delete()).hasSize(100);
        // 둘째 청크: 남은 delete 200
        assertThat(chunks.get(1).upsert()).isEmpty();
        assertThat(chunks.get(1).delete()).hasSize(200);
    }
}
