package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
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

    /**
     * 구독당 가장 최근 FAILED dispatch (generated_title IS NOT NULL, attempt_count < maxAttempts).
     * subscription_id별 MAX(id)를 서브쿼리로 구한 뒤 필터링.
     * updatedAt &lt; cooldown 조건으로 메인 배치와의 레이스 컨디션을 최소화한다.
     *
     * @param status      {@link DispatchStatus#FAILED}
     * @param maxAttempts {@link dev.jazzybyte.onseoul.notification.domain.NotificationDispatch#MAX_ATTEMPTS}
     * @param cooldown    이 시각 이전에 업데이트된 dispatch만 대상 (최소 안정화 대기)
     */
    @Query("""
            SELECT d FROM NotificationDispatchJpaEntity d
            WHERE d.status = :status
              AND d.generatedTitle IS NOT NULL
              AND d.attemptCount < :maxAttempts
              AND d.updatedAt < :cooldown
              AND d.id IN (
                  SELECT MAX(d2.id) FROM NotificationDispatchJpaEntity d2
                  WHERE d2.status = :status
                  GROUP BY d2.subscriptionId
              )
            """)
    List<NotificationDispatchJpaEntity> findRetryable(
            @Param("status") DispatchStatus status,
            @Param("maxAttempts") int maxAttempts,
            @Param("cooldown") Instant cooldown);
}
