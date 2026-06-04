package dev.jazzybyte.onseoul.collection.adapter.in.scheduler;

import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.event.CollectionCompletedEvent;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;

@Slf4j
@Component
@RequiredArgsConstructor
public class CollectionScheduler {

    private final CollectDatasetUseCase collectDatasetUseCase;
    private final ApplicationEventPublisher eventPublisher;

    /**
     * 매일 08:00 KST 수집 실행.
     *
     * <p>zone을 명시해 JVM 기본 타임존(배포 컨테이너는 UTC)과 무관하게
     * 한국시간 08:00에 고정 실행한다. zone 미지정 시 UTC 08:00(=17:00 KST)에 돌던 문제 수정.
     *
     * <p>수집 중 일부 소스가 실패해도 이벤트는 발행한다.
     * 성공한 소스에서 생긴 변경분에 대한 알림은 보내야 하기 때문이다.
     */
    @Scheduled(cron = "0 0 8 * * *", zone = "Asia/Seoul")
    public void scheduledCollect() {
        log.info("스케줄 수집 시작");
        // collectAll() 호출 직전 시각을 캡처한다. 임베딩 동기화 워커가 이 시각 이후
        // service_change_log.changed_at 을 "이번 run의 변경분"으로 식별한다.
        // 부분 실패해도 finally에서 이벤트를 발행하므로 시작 시각은 항상 전달된다.
        Instant runStartedAt = Instant.now();
        try {
            collectDatasetUseCase.collectAll();
        } finally {
            eventPublisher.publishEvent(new CollectionCompletedEvent(runStartedAt));
            log.info("수집 완료 이벤트 발행");
        }
    }
}
