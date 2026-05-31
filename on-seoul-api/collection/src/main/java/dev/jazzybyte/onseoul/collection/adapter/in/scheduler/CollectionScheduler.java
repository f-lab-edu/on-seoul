package dev.jazzybyte.onseoul.collection.adapter.in.scheduler;

import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.event.CollectionCompletedEvent;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class CollectionScheduler {

    private final CollectDatasetUseCase collectDatasetUseCase;
    private final ApplicationEventPublisher eventPublisher;

    /**
     * 매일 08:00 수집 실행.
     *
     * <p>수집 중 일부 소스가 실패해도 이벤트는 발행한다.
     * 성공한 소스에서 생긴 변경분에 대한 알림은 보내야 하기 때문이다.
     */
    @Scheduled(cron = "0 0 8 * * *")
    public void scheduledCollect() {
        log.info("스케줄 수집 시작");
        try {
            collectDatasetUseCase.collectAll();
        } finally {
            eventPublisher.publishEvent(new CollectionCompletedEvent());
            log.info("수집 완료 이벤트 발행");
        }
    }
}
