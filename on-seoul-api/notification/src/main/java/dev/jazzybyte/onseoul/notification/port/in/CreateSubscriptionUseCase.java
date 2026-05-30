package dev.jazzybyte.onseoul.notification.port.in;

import dev.jazzybyte.onseoul.notification.application.SubscriptionView;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.Set;

public interface CreateSubscriptionUseCase {

    SubscriptionView create(Long userId, CreateSubscriptionCommand cmd);

    record CreateSubscriptionCommand(
            String serviceId,
            SubscriptionFilter filter,
            Set<NotificationChannel> channels
    ) {}
}
