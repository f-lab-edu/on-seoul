package dev.jazzybyte.onseoul.collector;

import dev.jazzybyte.onseoul.domain.port.in.CollectDatasetUseCase;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Slf4j
@Component
@RequiredArgsConstructor
public class CollectionScheduler {

    private final CollectDatasetUseCase collectDatasetUseCase;

    @Scheduled(cron = "0 0 8 * * *")
    public void scheduledCollect() {
        log.info("스케줄 수집 시작");
        collectDatasetUseCase.collectAll();
    }
}
