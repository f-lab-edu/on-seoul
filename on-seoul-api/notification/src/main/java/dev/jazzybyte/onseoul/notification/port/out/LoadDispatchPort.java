package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.time.LocalDate;
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
     * <p><b>DEAD 상태의 불가역성:</b> 현재 구현에서 DEAD dispatch를 PENDING으로 되돌리는
     * 관리 API는 없다. 영구 실패 구독에서 알림을 재개하려면 해당 구독을 삭제 후 재생성해야 한다.
     * 이는 의도된 설계이며, 연락처 미등록 등 근본 원인이 해결되지 않은 상태에서의
     * 무한 재시도를 방지한다.
     *
     * @param subscriptionId 확인할 구독 ID
     */
    boolean existsDeadDispatchBySubscriptionId(Long subscriptionId);

    /**
     * 오늘({@code dispatchDate}) 같은 구독의 CHANGE dispatch payload 가 해당 서비스를 이미 커버하는지 조회한다.
     *
     * <p>CHANGE↔시점 cross-trigger dedup 강제용. 시점 잡이 dispatch 를 생성하기 <b>전</b> 호출해,
     * 같은 날 같은 구독의 CHANGE 가 이 service 를 이미 발송 대상에 묶었으면(JSONB containment
     * {@code notification_payload->'services' @> '[{"serviceId":"<sid>"}]'}) 시점 발행을 skip 한다
     * (CHANGE 우선). idx_nd_change_crossdedup(migration 12) 가 후보를 0~1행으로 좁힌다.
     *
     * <p>"오늘 범위"는 {@code dispatch_date} 컬럼이다(sent_at::date 아님 — PENDING 이면 sent_at NULL
     * 이라 누락된다). 따라서 CHANGE dispatch 가 dispatch_date 를 채워야 성립한다.
     *
     * @param subscriptionId 대상 구독
     * @param serviceId      대상 서비스 ID
     * @param dispatchDate   오늘(UTC 달력일)
     * @return CHANGE 가 이미 이 서비스를 오늘 커버하면 true
     */
    boolean existsChangeDispatchForServiceToday(Long subscriptionId, String serviceId, LocalDate dispatchDate);

    /**
     * 오늘 같은 구독·서비스에 시점 dispatch 가 이미 존재하는지 조회한다(시점-시점 dedup 선조회).
     *
     * <p>빈 batch 누적을 막기 위해 batch 생성 <b>전</b> 호출한다. 존재하면 시점 발행을 skip 한다.
     * {@code uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date)} 의 ON CONFLICT 멱등은
     * race-safety 백업으로 유지된다.
     *
     * @param subscriptionId 대상 구독
     * @param serviceId      대상 서비스 ID
     * @param dispatchDate   오늘(UTC 달력일)
     */
    boolean existsScheduledDispatch(Long subscriptionId, String serviceId, LocalDate dispatchDate);
}
