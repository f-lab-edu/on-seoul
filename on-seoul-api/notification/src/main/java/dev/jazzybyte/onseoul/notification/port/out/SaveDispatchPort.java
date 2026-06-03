package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.Optional;

public interface SaveDispatchPort {

    /**
     * (batch_id, subscription_id) 조합이 아직 없을 때만 INSERT 한다 (ON CONFLICT DO NOTHING 의미).
     * 중복일 경우 empty를 반환한다.
     */
    Optional<NotificationDispatch> saveIfAbsent(NotificationDispatch dispatch);

    /**
     * 시점 트리거 dispatch 를 멱등 INSERT 한다.
     * 부분 unique 인덱스 {@code uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date)
     * WHERE service_id IS NOT NULL} 를 이용해, 같은 날 같은 구독·같은 서비스에 대한 시점 알림은
     * 1건만 INSERT 한다(ON CONFLICT DO NOTHING 의미). 중복이면 empty 를 반환한다.
     *
     * <p>{@link #saveIfAbsent} 와 분리한 이유: 시점 dispatch 의 dedup 키는
     * (batch_id, subscription_id) 가 아니라 (subscription_id, service_id, dispatch_date) 다.
     * 한 시점 배치 안에서 한 구독이 service_id 가 다른 dispatch 를 여러 건 발행하므로
     * (batch_id, subscription_id) 멱등 제약을 그대로 쓰면 두 번째부터 모두 충돌한다.
     */
    Optional<NotificationDispatch> saveScheduledIfAbsent(NotificationDispatch dispatch);

    /** 기존 dispatch의 상태/내용 갱신. */
    NotificationDispatch save(NotificationDispatch dispatch);
}
