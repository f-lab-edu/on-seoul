package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.Instant;
import java.util.List;

public interface NotificationBatchJpaRepository
        extends JpaRepository<NotificationBatchJpaEntity, Long> {

    List<NotificationBatchJpaEntity> findByStatusAndStartedAtBefore(BatchStatus status, Instant startedAt);
}
