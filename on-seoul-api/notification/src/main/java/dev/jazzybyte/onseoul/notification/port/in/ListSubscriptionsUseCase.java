package dev.jazzybyte.onseoul.notification.port.in;

import dev.jazzybyte.onseoul.notification.application.SubscriptionView;

import java.util.List;

public interface ListSubscriptionsUseCase {

    /**
     * 주어진 사용자의 모든 알림 구독을 read model 형태로 반환한다.
     * filter 는 이미 파싱된 {@link dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter} 로 채워진다.
     */
    List<SubscriptionView> list(Long userId);
}
