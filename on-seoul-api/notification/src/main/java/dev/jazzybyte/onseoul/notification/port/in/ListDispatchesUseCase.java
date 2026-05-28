package dev.jazzybyte.onseoul.notification.port.in;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.List;

public interface ListDispatchesUseCase {

    /**
     * 사용자에게 발송된 알림 이력을 cursor 기반으로 페이지네이션하여 반환한다.
     *
     * @param userId 사용자 ID
     * @param cursor 직전 페이지 마지막 dispatch ID. null 이면 최신부터.
     * @param size   페이지 크기. 1..100 범위.
     */
    DispatchPage list(Long userId, Long cursor, int size);

    record DispatchPage(List<NotificationDispatch> dispatches, Long nextCursor) {}
}
