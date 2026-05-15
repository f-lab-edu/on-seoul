package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface NotificationDispatchJpaRepository
        extends JpaRepository<NotificationDispatchJpaEntity, Long> {

    @Query("SELECT d FROM NotificationDispatchJpaEntity d " +
           "WHERE d.subscriptionId = :subId " +
           "AND d.changeLogId = :changeId " +
           "AND d.status IN (dev.jazzybyte.onseoul.notification.domain.DispatchStatus.PENDING, " +
           "                 dev.jazzybyte.onseoul.notification.domain.DispatchStatus.FAILED) " +
           "AND d.attemptCount < :maxAttempts")
    Optional<NotificationDispatchJpaEntity> findRetryable(
            @Param("subId") Long subId,
            @Param("changeId") Long changeId,
            @Param("maxAttempts") int maxAttempts);

    Optional<NotificationDispatchJpaEntity> findBySubscriptionIdAndChangeLogId(
            Long subscriptionId, Long changeLogId);
}
