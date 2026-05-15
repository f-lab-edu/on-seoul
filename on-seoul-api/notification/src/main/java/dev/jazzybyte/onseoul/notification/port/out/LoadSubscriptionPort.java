package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;

import java.util.List;

public interface LoadSubscriptionPort {

    List<NotificationSubscription> loadAll();
}
