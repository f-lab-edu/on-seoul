package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

public interface LoadBatchPort {

    Optional<NotificationBatch> loadById(Long batchId);

    /**
     * JVM 크래시로 complete()/fail() 호출 없이 종료된 stale RUNNING batch를 조회한다.
     *
     * <p>{@code staleBefore} 이전에 시작된 RUNNING 상태 batch를 반환한다.
     * 호출자는 반환된 batch에 {@code fail(0, 0)}을 적용하여 FAILED로 전환한다.
     *
     * @param staleBefore 이 시각 이전에 startedAt인 RUNNING batch를 반환 (exclusive)
     */
    List<NotificationBatch> findStaleRunning(Instant staleBefore);
}
