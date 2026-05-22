package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.Optional;

public interface LoadDispatchPort {

    /** (batch_id, subscription_id) 키로 dispatch를 조회한다. */
    Optional<NotificationDispatch> loadByBatchAndSubscription(Long batchId, Long subscriptionId);
}
