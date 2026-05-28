package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;

public interface NotificationDispatchJpaRepository
        extends JpaRepository<NotificationDispatchJpaEntity, Long> {

    Optional<NotificationDispatchJpaEntity> findByBatchIdAndSubscriptionId(
            Long batchId, Long subscriptionId);

    /**
     * 사용자 ID 기준으로 dispatch 를 최신순(id DESC) 페이지네이션 조회.
     * notification_subscriptions JOIN 으로 소유권 필터링.
     */
    @Query("""
            SELECT d FROM NotificationDispatchJpaEntity d
            WHERE d.subscriptionId IN (
                SELECT s.id FROM NotificationSubscriptionJpaEntity s
                WHERE s.userId = :userId
            )
            AND (:cursor IS NULL OR d.id < :cursor)
            ORDER BY d.id DESC
            """)
    List<NotificationDispatchJpaEntity> findByUserIdWithCursor(
            @Param("userId") Long userId,
            @Param("cursor") Long cursor,
            Pageable pageable);
}
