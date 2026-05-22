package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface NotificationDispatchJpaRepository
        extends JpaRepository<NotificationDispatchJpaEntity, Long> {

    Optional<NotificationDispatchJpaEntity> findByBatchIdAndSubscriptionId(
            Long batchId, Long subscriptionId);
}
