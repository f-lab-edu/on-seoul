package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.List;
import java.util.Optional;

public interface LoadDispatchPort {

    /** (batch_id, subscription_id) 키로 dispatch를 조회한다. */
    Optional<NotificationDispatch> loadByBatchAndSubscription(Long batchId, Long subscriptionId);

    /**
     * 사용자에게 발송된 dispatch 를 {@code id DESC} 로 페이지네이션 조회한다.
     * {@code subscription_id} JOIN 으로 소유권 필터링.
     *
     * @param userId 사용자 ID
     * @param cursor null 이면 최신부터, 아니면 id < cursor
     * @param limit  페이지 크기 (size)
     */
    List<NotificationDispatch> loadByUserId(Long userId, Long cursor, int limit);

    /**
     * 구독당 가장 최근 FAILED dispatch를 반환한다.
     * generated_title IS NOT NULL (재시도 가능한 것만) AND attempt_count < 5.
     * 각 subscription_id별 id MAX인 것 1건.
     */
    List<NotificationDispatch> findRetryable();

    /**
     * 해당 구독에 DEAD 상태 dispatch가 하나라도 존재하면 true를 반환한다.
     *
     * <p>메인 배치 스케줄러가 영구 실패 구독에 대해 매 tick마다 새 dispatch를 생성하는 것을
     * 방지하기 위해 TX A 진입 직후 호출된다. DEAD dispatch가 있으면 발송 전체를 건너뛴다.
     *
     * @param subscriptionId 확인할 구독 ID
     */
    boolean existsDeadDispatchBySubscriptionId(Long subscriptionId);
}
