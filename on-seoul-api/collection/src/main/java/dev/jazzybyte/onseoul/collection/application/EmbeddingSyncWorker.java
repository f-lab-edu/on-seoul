package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.port.out.EmbeddingSyncPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort.ChangedServiceIds;
import dev.jazzybyte.onseoul.event.CollectionCompletedEvent;
import dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 임베딩 동기화 워커 — 처리 순서 보장의 핵심.
 *
 * <p>{@link CollectionCompletedEvent} 수신 → 이번 run의 변경 service_id 조회 →
 * AI 서비스에 임베딩 동기화 요청 → {@link EmbeddingSyncCompletedEvent} 발행.
 * 이로써 수집 → 임베딩 동기화 → 알림 순서가 이벤트 체인으로 보장된다.
 *
 * <p>변경 service_id를 아는 것은 collection BC의 책임이므로 이 워커는 collection 모듈에 둔다.
 *
 * <p>실패 정책: AI 호출 실패해도 예외를 삼키지 않고 로그를 남기되, finally에서 항상
 * {@link EmbeddingSyncCompletedEvent}를 발행한다. 임베딩 실패가 알림 발송을 막아서는
 * 안 되기 때문이다(best-effort). CollectionScheduler가 finally에서 CollectionCompletedEvent를
 * 발행하는 것과 동일한 패턴이다.
 */
@Slf4j
@Component
public class EmbeddingSyncWorker {

    /**
     * 단일 AI 호출의 upsert+delete 합계 상한. AI 서비스 제약(≤ 500)에 맞춘다.
     * 이를 초과하면 청크로 분할해 여러 번 호출한다.
     */
    static final int MAX_BATCH = 500;

    private final LoadChangedServiceIdsPort loadChangedServiceIdsPort;
    private final EmbeddingSyncPort embeddingSyncPort;
    private final ApplicationEventPublisher eventPublisher;

    /**
     * 중복 실행 방지 플래그. CollectionCompletedEvent가 중복 발행(수동 트리거 + 스케줄 겹침 등)돼도
     * 두 워커가 같은 loadSince 윈도우로 AI sync를 중복 호출하지 않게 한다.
     * NotificationScheduler의 동일 패턴을 따른다.
     */
    private final AtomicBoolean running = new AtomicBoolean(false);

    public EmbeddingSyncWorker(final LoadChangedServiceIdsPort loadChangedServiceIdsPort,
                               final EmbeddingSyncPort embeddingSyncPort,
                               final ApplicationEventPublisher eventPublisher) {
        this.loadChangedServiceIdsPort = loadChangedServiceIdsPort;
        this.embeddingSyncPort = embeddingSyncPort;
        this.eventPublisher = eventPublisher;
    }

    @Async
    @EventListener
    public void onCollectionCompleted(CollectionCompletedEvent event) {
        if (!running.compareAndSet(false, true)) {
            // 이미 실행 중 — 같은 loadSince 윈도우로 AI sync를 중복 호출하지 않도록 본문을 건너뛴다.
            // 진행 중인 워커가 자신의 finally에서 EmbeddingSyncCompletedEvent를 발행하므로
            // 여기서 발행하지 않아도 알림 체인은 끊기지 않는다(NotificationScheduler도 AtomicBoolean으로
            // 중복 이벤트를 무시하므로 중복 발행 자체는 안전하지만, 불필요한 발행은 생략한다).
            log.warn("[EmbeddingSyncWorker] 이미 실행 중 — 이벤트 무시 (진행 중 워커가 완료 이벤트 발행)");
            return;
        }
        try {
            syncEmbeddings(event);
        } catch (Exception e) {
            // 예외를 삼키지 않고 로그로 남기되, 알림 흐름을 막지 않도록 전파하지 않는다.
            log.error("[EmbeddingSyncWorker] 임베딩 동기화 실패 — 알림은 계속 진행: error={}",
                    e.getMessage(), e);
        } finally {
            running.set(false);
            eventPublisher.publishEvent(new EmbeddingSyncCompletedEvent());
            log.info("[EmbeddingSyncWorker] 임베딩 동기화 완료 이벤트 발행");
        }
    }

    private void syncEmbeddings(CollectionCompletedEvent event) {
        ChangedServiceIds changed = loadChangedServiceIdsPort.loadSince(event.runStartedAt());

        if (changed.isEmpty()) {
            log.debug("[EmbeddingSyncWorker] 변경 service_id 없음 — AI 호출 생략");
            return;
        }

        log.info("[EmbeddingSyncWorker] 변경 감지 — upsert={}, delete={}",
                changed.upsert().size(), changed.delete().size());

        // AI 엔드포인트는 같은 service_id가 upsert/delete에 동시 존재하면 422를 던진다.
        // 현재는 "한 run에서 조회된 service_id가 같은 run의 삭제 sweep 대상이 아니다"라는 구조적
        // 불변식에 의존하지만, 향후 deletion-sweep 변경으로 조용히 겹침이 생길 수 있다.
        // upsert 우선으로 delete에서 겹치는 id를 제거해 422로 인한 청크 전체 임베딩 누락을 방어한다.
        List<String> upsert = changed.upsert();
        List<String> delete = removeOverlap(changed.delete(), upsert);

        for (Chunk chunk : split(upsert, delete)) {
            embeddingSyncPort.sync(chunk.upsert(), chunk.delete());
        }
    }

    /**
     * {@code delete}에서 {@code upsert}와 겹치는 service_id를 제거한다(upsert 우선).
     * 원본 순서를 보존한다.
     */
    private static List<String> removeOverlap(List<String> delete, List<String> upsert) {
        Set<String> upsertSet = new LinkedHashSet<>(upsert);
        return delete.stream().filter(id -> !upsertSet.contains(id)).toList();
    }

    /**
     * upsert+delete 합계가 {@link #MAX_BATCH}를 넘지 않도록 청크로 분할한다.
     * upsert를 먼저 채우고 남은 용량을 delete로 채운다.
     */
    static List<Chunk> split(List<String> upsert, List<String> delete) {
        List<Chunk> chunks = new ArrayList<>();
        int ui = 0;
        int di = 0;
        while (ui < upsert.size() || di < delete.size()) {
            int remaining = MAX_BATCH;
            int uEnd = Math.min(upsert.size(), ui + remaining);
            List<String> uPart = upsert.subList(ui, uEnd);
            remaining -= uPart.size();
            int dEnd = Math.min(delete.size(), di + remaining);
            List<String> dPart = delete.subList(di, dEnd);

            chunks.add(new Chunk(List.copyOf(uPart), List.copyOf(dPart)));
            ui = uEnd;
            di = dEnd;
        }
        return chunks;
    }

    /**
     * 단일 AI sync 호출에 담을 service_id 묶음. upsert+delete 합계가 {@link #MAX_BATCH} 이하다.
     */
    record Chunk(List<String> upsert, List<String> delete) {
    }
}
