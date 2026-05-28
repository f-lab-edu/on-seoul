package dev.jazzybyte.onseoul.notification.port.in;

import dev.jazzybyte.onseoul.notification.application.SubscriptionView;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.Set;

public interface UpdateSubscriptionUseCase {

    SubscriptionView update(Long userId, Long subscriptionId, UpdateSubscriptionCommand cmd);

    /** filter/channels 모두 null 가능 — null 인 필드는 변경하지 않는다. */
    record UpdateSubscriptionCommand(
            SubscriptionFilter filter,
            Set<NotificationChannel> channels
    ) {}
}
