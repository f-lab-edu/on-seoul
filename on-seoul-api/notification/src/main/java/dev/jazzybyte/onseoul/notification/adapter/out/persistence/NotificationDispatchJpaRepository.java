package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface NotificationDispatchJpaRepository
        extends JpaRepository<NotificationDispatchJpaEntity, Long> {

    Optional<NotificationDispatchJpaEntity> findByBatchIdAndSubscriptionId(
            Long batchId, Long subscriptionId);

    boolean existsBySubscriptionIdAndStatus(Long subscriptionId, DispatchStatus status);

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

    /**
     * 오늘 같은 구독의 CHANGE dispatch payload 가 해당 service 를 커버하는지(JSONB containment).
     *
     * <p>네이티브 쿼리 — PostgreSQL JSONB {@code @>} 연산자를 쓴다(JPQL 미지원). idx_nd_change_crossdedup
     * (subscription_id, dispatch_date) WHERE trigger_type='CHANGE' 로 후보를 0~1행으로 좁힌 뒤
     * 그 행에 대해서만 containment 를 평가한다.
     *
     * <p>{@code :servicesJson} 은 JSON <b>배열</b> 리터럴 {@code '[{"serviceId":"X"}]'} 이어야 하며
     * ({@code '{"serviceId":"X"}'} 단일 객체는 배열⊇객체 비교가 되지 않아 항상 false), {@code ::jsonb}
     * 로 명시 캐스팅한다.
     *
     * <p>H2 는 {@code @>} / {@code ::jsonb} 를 지원하지 않아 이 쿼리는 PostgreSQL 에서만 동작한다
     * (QA 가 PG 로 containment 매칭을 검증한다).
     */
    @Query(value = """
            SELECT EXISTS (
                SELECT 1 FROM notification_dispatches
                WHERE subscription_id = :subscriptionId
                  AND trigger_type = 'CHANGE'
                  AND dispatch_date = :dispatchDate
                  AND notification_payload -> 'services' @> CAST(:servicesJson AS jsonb)
            )
            """, nativeQuery = true)
    boolean existsChangeDispatchCoveringService(
            @Param("subscriptionId") Long subscriptionId,
            @Param("dispatchDate") LocalDate dispatchDate,
            @Param("servicesJson") String servicesJson);

    /**
     * 오늘 같은 구독·서비스에 시점 dispatch 가 존재하는지(시점-시점 dedup 선조회).
     * 일반 컬럼 등치 조건이라 H2/PG 공통으로 동작한다.
     */
    boolean existsBySubscriptionIdAndServiceIdAndDispatchDate(
            Long subscriptionId, String serviceId, LocalDate dispatchDate);
}
