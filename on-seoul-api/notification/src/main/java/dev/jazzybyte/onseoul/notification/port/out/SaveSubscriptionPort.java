package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;

public interface SaveSubscriptionPort {

    NotificationSubscription save(NotificationSubscription subscription);
}
