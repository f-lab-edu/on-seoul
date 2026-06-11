package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.port.out.AiCacheFlushPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort.ChangedServiceIds;
import dev.jazzybyte.onseoul.event.EmbeddingSyncCompletedEvent;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/**
 * 데이터·임베딩 동기화 완료 후 AI answer cache를 무효화하는 핸들러.
 *
 * <p>trigger: {@link EmbeddingSyncCompletedEvent}. answer cache는 정형 데이터 + 임베딩 둘 다에
 * 의존하므로, 임베딩 적재까지 끝난 이 시점에 flush해야 벡터 기반 답변까지 최신 기준이 된다.
 * 이 이벤트는 EmbeddingSyncWorker가 finally(성공/실패 무관)에서 발행하므로, 임베딩이 실패해도
 * 최신 데이터를 반영하기 위해 flush가 시도된다.
 *
 * <p>변경 유무 판정: {@code service_change_log}를 {@code runStartedAt} 이후로 조회해
 * 이번 수집 사이클에 NEW/UPDATED/DELETED(삭제 포함) 변경이 있었는지 본다. 변경이 0이면 flush를
 * 생략한다(불필요한 캐시 무효화로 인한 thundering herd 방지). 경계 식별은 EmbeddingSyncWorker가
 * 쓰는 {@link LoadChangedServiceIdsPort#loadSince(java.time.Instant)}를 재사용한다 — 동일 기준이라
 * 임베딩 sync 대상과 flush 판정이 일관된다.
 *
 * <p>best-effort: flush 호출 실패(타임아웃/5xx/401/네트워크)나 변경 조회 실패가 이벤트 체인을
 * 끊지 않도록 try/catch로 WARN 로그만 남기고 정상 종료한다. 캐시는 TTL로도 만료된다.
 */
@Slf4j
@Component
public class AiCacheFlushHandler {

    private final LoadChangedServiceIdsPort loadChangedServiceIdsPort;
    private final AiCacheFlushPort aiCacheFlushPort;

    public AiCacheFlushHandler(final LoadChangedServiceIdsPort loadChangedServiceIdsPort,
                               final AiCacheFlushPort aiCacheFlushPort) {
        this.loadChangedServiceIdsPort = loadChangedServiceIdsPort;
        this.aiCacheFlushPort = aiCacheFlushPort;
    }

    @Async
    @EventListener
    public void onEmbeddingSyncCompleted(EmbeddingSyncCompletedEvent event) {
        try {
            ChangedServiceIds changed = loadChangedServiceIdsPort.loadSince(event.runStartedAt());
            if (changed.isEmpty()) {
                log.debug("[AiCacheFlush] 이번 사이클 변경 없음 — flush 생략");
                return;
            }
            log.info("[AiCacheFlush] 변경 감지 — AI answer cache flush 요청");
            aiCacheFlushPort.flush();
        } catch (Exception e) {
            // best-effort: flush/조회 실패가 수집·임베딩·알림 체인을 막아서는 안 된다. 캐시는 TTL로도 만료.
            log.warn("[AiCacheFlush] AI 캐시 flush 실패 — 무시(캐시는 TTL로 만료): error={}", e.getMessage());
        }
    }
}
