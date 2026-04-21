package dev.jazzybyte.onseoul.scheduler;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class CollectionScheduler {

    private final CollectionService collectionService;

    /**
     * 매일 08시 수집 실행.
     * 수집 실패 시 CollectionService 내부에서 이력을 기록하고 예외는 삼킨다.
     */
    @Scheduled(cron = "0 0 8 * * *")
    public void scheduledCollect() {
        log.info("스케줄 수집 시작");
        collectionService.collectAll();
    }
}
