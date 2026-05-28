package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;

import java.util.List;
import java.util.Optional;

public interface LoadSubscriptionPort {

    List<NotificationSubscription> loadAll();

    List<NotificationSubscription> loadByUserId(Long userId);

    Optional<NotificationSubscription> loadById(Long id);
}
