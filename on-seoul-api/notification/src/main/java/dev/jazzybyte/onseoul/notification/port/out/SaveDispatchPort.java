package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.Optional;

public interface SaveDispatchPort {

    /**
     * (batch_id, subscription_id) 조합이 아직 없을 때만 INSERT 한다 (ON CONFLICT DO NOTHING 의미).
     * 중복일 경우 empty를 반환한다.
     */
    Optional<NotificationDispatch> saveIfAbsent(NotificationDispatch dispatch);

    /** 기존 dispatch의 상태/내용 갱신. */
    NotificationDispatch save(NotificationDispatch dispatch);
}
